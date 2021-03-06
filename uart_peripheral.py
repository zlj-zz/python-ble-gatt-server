import sys
sys.path.append('.')
import os
import pathlib
import hashlib
import time
import json
import logging
import threading
import dbus, dbus.mainloop.glib
from gi.repository import GLib
try:
    from gi.repository import GObject
except ImportError:
    import gobject as GObject

from ai.bt.example_advertisement import Advertisement
from ai.bt.example_advertisement import register_ad_cb, register_ad_error_cb
from ai.bt.example_gatt_server import Service, Characteristic
from ai.bt.example_gatt_server import register_app_cb, register_app_error_cb
import ai.featurequeue
import ai.marsglobal
import ai.action.move.movement
import ai.action.eyedisplay.eyedisplay
import ai.parameters

control_handle = ai.action.move.movement.Movements()

BLUEZ_SERVICE_NAME = 'org.bluez'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
UART_SERVICE_UUID = '6e400001-b5a3-f393-00a9-e50e24dcca9e'
UART_RX_CHARACTERISTIC_UUID = '6e400012-b5a3-f393-00a9-e50e24dcca9e'
UART_TX_CHARACTERISTIC_UUID = '6e400013-b5a3-f393-00a9-e50e24dcca9e'
mainloop = None
global fb
fb = None
i = 0


class RxCharacteristic(Characteristic):
    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_RX_CHARACTERISTIC_UUID,
                                ['write'], service)

    def WriteValue(self, value, options):
        bytes(value).decode()


class PlayCharacteristic(Characteristic):
    """
    The characteristic is used for the play page of the app.
    """
    if_stop = False
    is_doing = False
    doing_lock = threading.Lock()
    func_name = ''

    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_TX_CHARACTERISTIC_UUID,
                                ['read', 'write', 'notify'], service)

        self.notifying = False
        self.notify_type = 'Move'
        self.notify_type_list = [
            'Voice', 'Vision', 'Body Sensor', 'Distance Sensor', 'Gyro Sensor'
        ]
        #GLib.io_add_watch(sys.stdin, GLib.IO_IN, self.notify_voic_command)
        self.turn_left_thread = None
        self.turn_right_thread = None
        self.run_thread = None
        self.walk_thread = None
        self.backward_thread = None

    def WriteValue(self, value, options):
        """
        Let client to write data to here.
        Receive notify type data to change the notify type.
        Receive command to enable and disable the control mode.
        """
        s = bytes(value).decode()
        if s in self.notify_type_list:
            self.notify_type = s
        elif s == 'enable_control_mode':
            ai.marsglobal.MarsControl.control_mode = True
        elif s == 'disable_control_mode':
            ai.marsglobal.MarsControl.control_mode = False
        elif ai.marsglobal.MarsControl.control_mode:
            self.ble_move_control(s)

    def ReadValue(self, options):
        """
        Let client to read data from here.
        """
        return [dbus.Byte(1)]

    def StartNotify(self):
        if self.notifying:
            return

        self.notifying = True
        self.toggle_notification()
        print('start notifying')

    def StopNotify(self):
        if not self.notifying:
            return

        self.notifying = False
        self.toggle_notification()
        print('stop notifying')

    def check_if_clear_data(self):
        if ai.marsglobal.MarsControl.ble_clear_counter > 3:
            ai.marsglobal.MarsControl.ble_clear_counter = 0
            ai.featurequeue.FeatureQueue.ble_clear()

    def notify_counter(self):
        ai.marsglobal.MarsControl.ble_clear_counter += 1
        self.check_if_clear_data()

    def notify_any(self):
        if not self.notifying:
            return

        self.notify_counter()

        feature_dict = ai.featurequeue.FeatureQueue.ble_get()
        send_dict = {}

        if ai.marsglobal.MarsControl.voice_listening:
            listen_data = '1'
        else:
            listen_data = '0'
        send_dict['listen'] = listen_data

        if 20 in feature_dict.keys():
            vocie_data = feature_dict[20]['word']
            vocie_data = vocie_data[0].upper() + vocie_data[1:].lower()
        else:
            vocie_data = ' '
        send_dict['voice'] = vocie_data

        if 30 in feature_dict.keys():
            vision_data = 'face'
        elif 40 in feature_dict.keys():
            vision_data = 'rat'
        elif 110 in feature_dict.keys():
            vision_data = 'ball'
        else:
            vision_data = ' '
        send_dict['vision'] = vision_data

        if 10 in feature_dict.keys():
            touch_list = feature_dict[10]
            if touch_list[3] == 0:
                touch_data = 'jaw'
            elif touch_list[2] == 0:
                touch_data = 'body'
            else:
                touch_data = 'other'
        else:
            touch_data = 'other'
        send_dict['touch'] = touch_data

        if 70 in feature_dict.keys():
            distance_data = feature_dict[70]
            if distance_data > 200:
                distance_data = 200
            elif distance_data < 0:
                distance_data = 200
        else:
            distance_data = 200
        send_dict['distance'] = distance_data

        if 80 in feature_dict.keys():
            gyro_data = feature_dict[80]
            gyro_list = [
                round(float(gyro_data[0]), 2),
                round(float(gyro_data[1]), 2)
            ]
        else:
            gyro_list = [0.0, 0.0]
        send_dict['gyro'] = gyro_list

        data = json.dumps(send_dict)

        value = []
        for b in data:
            value.append(dbus.Byte(b.encode()))

        self.PropertiesChanged(GATT_CHRC_IFACE, {'Value': value}, [])

    def do_notify(self):
        """
        According the receive command to change the notify data.
        """

        self.notify_any()
        return True

    def toggle_notification(self):
        if not self.notifying:
            return

        # each 1s notify one time.
        GObject.timeout_add(1000, self.do_notify)

    def do_action_thread(self):
        """
        Feedback control command, start the corresponding action thread.
        """
        PlayCharacteristic.doing_lock.acquire()
        while True:
            if PlayCharacteristic.if_stop:
                break
            # control_handle.ble_turn_left()
            try:
                getattr(control_handle, PlayCharacteristic.func_name)()
            except Exception as e:
                print(e)

            print('finished at once', PlayCharacteristic.func_name)
        PlayCharacteristic.doing_lock.release()

    def ble_move_control(self, s):
        print(ai.parameters.BLE_MOVE[s])
        if s == 'stand' or s == 'sit' or s == 'lie_down':
            getattr(control_handle, ai.parameters.BLE_MOVE[s])()
        elif ai.parameters.BLE_MOVE[s] != '':
            PlayCharacteristic.if_stop = False
            PlayCharacteristic.func_name = ai.parameters.BLE_MOVE[s]
            self.run_thread = threading.Thread(target=self.do_action_thread)
            self.run_thread.start()
        else:
            PlayCharacteristic.if_stop = True


class UpdateCharacteristic(Characteristic):
    """
    The characteristic is used for updating the MarsAI.
    """
    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_UD_CHARACTERISTIC_UUID,
                                ['write', 'read'], service)
        self.md5 = False

    def ReadValue(self, options):
        """
        Send marsai version
        """
        with open(ai.parameters.VERSION_PATH) as f:
            data = f.read()

        value = []
        for b in data:
            value.append(dbus.Byte(b.encode()))
        return value

    def WriteValue(self, value, options):
        """
        Reveice update file and check file MD5.
        """
        global fb
        global i
        if bytes(value) == b'SUD':
            i = 0
            if os.path.exists(str(pathlib.Path.home()) + '/update.zip'):
                os.system(
                    'rm {}'.format(str(pathlib.Path.home()) + '/update.zip'))
            os.system(
                'touch {}'.format(str(pathlib.Path.home()) + '/update.zip'))
            fb = open(str(pathlib.Path.home()) + '/update.zip', 'ab+')
        elif bytes(value) == b'EOM':
            fb.close()
        elif bytes(value) == b'MD5':
            self.md5 = True
        elif self.md5:
            self.md5 = False

            file_md5 = bytearray(value).decode()

            md5 = self.get_md5(str(pathlib.Path.home()) + '/update.zip')

            if file_md5 == md5:
                os.system('unzip -o ~/update.zip -d ~/update')
                time.sleep(1)
                os.system('reboot')
            else:
                raise Exception('md5 not same')
        else:
            fb.write(bytes(value))
            i += 1
            print('\r write data times: [{}]'.format(i), end='')

    def get_md5(self, _file):
        m = hashlib.md5()
        with open(_file, 'rb') as f:
            for line in f:
                m.update(line)
        md5_code = m.hexdigest()
        return md5_code


class UartService(Service):
    def __init__(self, bus, index):
        Service.__init__(self, bus, index, UART_SERVICE_UUID, True)
        self.add_characteristic(PlayCharacteristic(bus, 0, self))
        self.add_characteristic(UpdateCharacteristic(bus, 2, self))


class Application(dbus.service.Object):
    def __init__(self, bus):
        self.path = '/'
        self.services = []
        dbus.service.Object.__init__(self, bus, self.path)

    def get_path(self):
        return dbus.ObjectPath(self.path)

    def add_service(self, service):
        self.services.append(service)

    @dbus.service.method(DBUS_OM_IFACE, out_signature='a{oa{sa{sv}}}')
    def GetManagedObjects(self):
        response = {}
        for service in self.services:
            response[service.get_path()] = service.get_properties()
            chrcs = service.get_characteristics()
            for chrc in chrcs:
                response[chrc.get_path()] = chrc.get_properties()
        return response


class UartApplication(Application):
    def __init__(self, bus):
        Application.__init__(self, bus)
        self.add_service(UartService(bus, 0))


class UartAdvertisement(Advertisement):
    def __init__(self, bus, index):
        Advertisement.__init__(self, bus, index, 'peripheral')
        self.add_service_uuid(UART_SERVICE_UUID)
        self.add_local_name(ai.parameters.CAT_BLE_NAME)
        self.include_tx_power = True


def find_adapter(bus):
    remote_om = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, '/'),
                               DBUS_OM_IFACE)
    objects = remote_om.GetManagedObjects()
    for o, props in objects.items():
        if LE_ADVERTISING_MANAGER_IFACE in props and GATT_MANAGER_IFACE in props:
            return o
        print('Skip adapter:', o)
    return None


def main():
    global mainloop
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SystemBus()
    adapter = find_adapter(bus)
    if not adapter:
        print('BLE adapter not found')
        return

    service_manager = dbus.Interface(
        bus.get_object(BLUEZ_SERVICE_NAME, adapter), GATT_MANAGER_IFACE)
    ad_manager = dbus.Interface(bus.get_object(BLUEZ_SERVICE_NAME, adapter),
                                LE_ADVERTISING_MANAGER_IFACE)

    app = UartApplication(bus)
    adv = UartAdvertisement(bus, 0)

    mainloop = GLib.MainLoop()

    service_manager.RegisterApplication(app.get_path(), {},
                                        reply_handler=register_app_cb,
                                        error_handler=register_app_error_cb)
    ad_manager.RegisterAdvertisement(adv.get_path(), {},
                                     reply_handler=register_ad_cb,
                                     error_handler=register_ad_error_cb)
    try:
        mainloop.run()
    except KeyboardInterrupt:
        adv.Release()


if __name__ == '__main__':
    main()
