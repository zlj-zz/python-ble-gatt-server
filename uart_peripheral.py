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

from .example_advertisement import Advertisement
from .example_advertisement import register_ad_cb, register_ad_error_cb
from .example_gatt_server import Service, Characteristic
from .example_gatt_server import register_app_cb, register_app_error_cb

BLUEZ_SERVICE_NAME = 'org.bluez'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
UART_SERVICE_UUID = '6e400001-b5a3-f393-00a9-e50e24dcca7d'
UART_RX_CHARACTERISTIC_UUID = '6e400012-b5a3-f393-00a9-e50e24dcca7d'
UART_TX_CHARACTERISTIC_UUID = '6e400013-b5a3-f393-00a9-e50e24dcca7d'
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

    def __init__(self, bus, index, service):
        Characteristic.__init__(self, bus, index, UART_TX_CHARACTERISTIC_UUID,
                                ['read', 'write', 'notify'], service)

        self.notifying = False
        #GLib.io_add_watch(sys.stdin, GLib.IO_IN, self.notify_voic_command)

    def WriteValue(self, value, options):
        """
        Let client to write data to here.
        Receive notify type data to change the notify type.
        Receive command to enable and disable the control mode.
        """
        s = bytes(value).decode()
        
        # next processing.
        pass

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

    def notify_any(self):
        if not self.notifying:
            return
          
        # main context.
        pass

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
        with open('./version.txt') as f:
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
                os.remove(str(pathlib.Path.home()) + '/update.zip')
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
