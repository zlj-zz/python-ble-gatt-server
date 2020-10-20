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
import ai.coloredlogging
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
UART_RX_CHARACTERISTIC_UUID = '6e400002-b5a3-f393-00a9-e50e24dcca9e'
UART_TX_CHARACTERISTIC_UUID = '6e400003-b5a3-f393-00a9-e50e24dcca9e'
UART_UD_CHARACTERISTIC_UUID = '6e400004-b5a3-f393-00a9-e50e24dcca9e'
UART_BE_CHARACTERISTIC_UUID = '6e400005-b5a3-f393-00a9-e50e24dcca9e'
UART_SE_CHARACTERISTIC_UUID = '6e400006-b5a3-f393-00a9-e50e24dcca9e'
mainloop = None
global fb
fb = None
i = 0


class RxCharacteristic(Characteristic):
    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_RX_CHARACTERISTIC_UUID,
                                ['write'], service)
        self.logger = ai.coloredlogging.get_logger("Move",
                                                   ai.coloredlogging.BLUE)
        self.logger.addHandler(logging.NullHandler())

    def WriteValue(self, value, options):
        self.logger.debug('remote: {}'.format(bytearray(value).decode()))
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
        self.logger = ai.coloredlogging.get_logger("Play",
                                                   ai.coloredlogging.WHITE)
        self.logger.addHandler(logging.NullHandler())

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
        Receive action command to control MarsCat.
        """
        self.logger.debug('recive type: {}'.format(bytearray(value).decode()))
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
            self.logger.debug('Already notifying, nothing to do')
            return

        self.notifying = True
        self.toggle_notification()
        self.logger.debug('start notifying')

    def StopNotify(self):
        if not self.notifying:
            self.logger.debug('Not notifying, nothing to do')
            return

        self.notifying = False
        self.toggle_notification()
        self.logger.debug('stop notifying')

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
            # elif l[1] == 0 or l[2] == 0:
            # data = 'head'
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
        self.logger.debug('data : [{}]'.format(data))

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
        self.logger = ai.coloredlogging.get_logger("Update",
                                                   ai.coloredlogging.GREEN)
        self.logger.addHandler(logging.NullHandler())

    def ReadValue(self, options):
        """
        Send marsai version
        """
        self.logger.debug('>>>>>>> Update Characteristic [read]')
        with open(ai.parameters.VERSION_PATH) as f:
            data = f.read()

        self.logger.debug('read: ' + repr(data))

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
            self.logger.debug('create ZIP file and wait recive')
            if os.path.exists(str(pathlib.Path.home()) + '/marsai.zip'):
                os.system(
                    'rm {}'.format(str(pathlib.Path.home()) + '/marsai.zip'))
            os.system(
                'touch {}'.format(str(pathlib.Path.home()) + '/marsai.zip'))
            fb = open(str(pathlib.Path.home()) + '/marsai.zip', 'ab+')
        elif bytes(value) == b'EOM':
            fb.close()
            self.logger.debug('revice over')
        elif bytes(value) == b'MD5':
            self.md5 = True
            self.logger.debug('prepare to verify MD5')
        elif self.md5:
            self.md5 = False

            file_md5 = bytearray(value).decode()
            self.logger.debug('recive MD5: {}'.format(file_md5))

            md5 = self.get_md5(str(pathlib.Path.home()) + '/marsai.zip')
            self.logger.debug('local file MD5: {}'.format(md5))

            if file_md5 == md5:
                os.system('unzip -o ~/marsai.zip -d ~/marsai')
                time.sleep(1)
                os.system('reboot')
                self.logger.debug('update ok')
            else:
                self.logger.debug('md5 error')
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


class BasicCharacteristic(Characteristic):
    """
    The characteristic is used for the Basic page of the app.
    Read basic information and modify basic setting.
    """
    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_BE_CHARACTERISTIC_UUID,
                                ['write', 'read'], service)
        self.logger = ai.coloredlogging.get_logger("Basic",
                                                   ai.coloredlogging.YELLOW)
        self.logger.addHandler(logging.NullHandler())

    def WriteValue(self, value, options):
        """
        Rewrite the modified value to the file of the cat's setting.
        """
        self.logger.debug('>>>>>>> Basic Characteristic [write]')
        self.logger.debug('remote: {}'.format(bytearray(value).decode()))
        info_list = bytes(value).decode().split('-')

        # write to personality.json
        with open(ai.parameters.PERSONALITY_SAVE_PATH, 'r') as f:
            data = json.load(f)
            data['energetic'] = float(info_list[0])
            data['enthusiastic'] = float(info_list[1])
            data['social'] = float(info_list[2])
        with open(ai.parameters.PERSONALITY_SAVE_PATH, 'w') as f:
            json.dump(data, f)

        # write to setting.json
        with open(ai.parameters.SETTING_PATH, 'r') as fs:
            data = json.load(fs)
            data['eye'] = info_list[3]
            data['sex'] = info_list[4]
            data['mute'] = info_list[5]
        with open(ai.parameters.SETTING_PATH, 'w') as fs:
            json.dump(data, fs)
        self.logger.debug('modify over')
        if ai.action.eyedisplay.eyedisplay.EyeDisplay.eye != data['eye']:
            ai.action.eyedisplay.eyedisplay.EyeDisplay.reset_eye_background()
        mute = True if data['mute'] == 'true' else False
        if ai.marsglobal.MarsControl.mute != mute:
            ai.marsglobal.MarsControl.mute = mute

    def ReadValue(self, options):
        """
        Read cat's basic info from local file.
        the personality value to one decimal place.
        """
        self.logger.debug('>>>>>>> Basic Characteristic [read]')
        with open(ai.parameters.PERSONALITY_SAVE_PATH) as f:
            personality_data = json.load(f)
        self.logger.debug(personality_data)
        energetic = round(personality_data['energetic'], 1)
        enthusiastic = round(personality_data['enthusiastic'], 1)
        social = round(personality_data['social'], 1)

        with open(ai.parameters.SETTING_PATH) as fs:
            data = json.load(fs)
        self.logger.debug(data)
        eye = data['eye']
        sex = data['sex']
        mute = data['mute']

        battery_value = control_handle.ble_get_battery()

        v = '{}-{}-{}-{}-{}-{}-{}'.format(energetic, enthusiastic, social, eye,
                                          sex, mute, battery_value)
        self.logger.debug('read: ' + repr(v))

        value = []
        for b in v:
            value.append(dbus.Byte(b.encode()))
        return value


class StateCharacteristic(Characteristic):
    """
    The characteristic is used for the Stats page of the app.
    """
    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_SE_CHARACTERISTIC_UUID,
                                ['read'], service)
        self.logger = ai.coloredlogging.get_logger("State",
                                                   ai.coloredlogging.RED)
        self.logger.addHandler(logging.NullHandler())

    def ReadValue(self, options):
        self.logger.debug('>>>>>>> State Characteristic [read]')
        with open(ai.parameters.PERSONALITY_SAVE_PATH) as f:
            personality_data = json.load(f)
        self.logger.debug(str(personality_data))
        with open(ai.parameters.RECORD_STATE_PATH) as f:
            state_data = json.load(f)
        self.logger.debug(str(state_data))

        S = (personality_data['energetic'] + personality_data['enthusiastic'] +
             personality_data['social']) / 3
        level = 0
        experience = 0
        if S < 25:
            level = 1
            experience = S / 25 * 100
        elif S < 50:
            level = 2
            experience = (S - 25) / 25 * 100
        elif S < 75:
            level = 3
            experience = (S - 50) / 25 * 100
        elif S <= 100:
            level = 4
            experience = (S - 75) / 25 * 100
        self.logger.debug('experience: {}, level: {}'.format(
            experience, level))

        v = '{}-{}-{}-{}-{}-{}-{}'.format(level, int(experience),
                                          state_data['touch_count'],
                                          state_data['voice_count'],
                                          state_data['vision_count'],
                                          state_data['sleep_count'],
                                          state_data['self_play_count'])
        self.logger.debug('read: ' + repr(v))

        value = []
        for b in v:
            value.append(dbus.Byte(b.encode()))
        return value


class UartService(Service):
    def __init__(self, bus, index):
        Service.__init__(self, bus, index, UART_SERVICE_UUID, True)
        self.add_characteristic(PlayCharacteristic(bus, 0, self))
        #self.add_characteristic(RxCharacteristic(bus, 1, self))
        self.add_characteristic(UpdateCharacteristic(bus, 2, self))
        self.add_characteristic(BasicCharacteristic(bus, 3, self))
        self.add_characteristic(StateCharacteristic(bus, 4, self))


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
