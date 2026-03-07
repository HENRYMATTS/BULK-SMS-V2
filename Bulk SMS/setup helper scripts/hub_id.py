'''
Plug in hub and run this to get its hub_id to be used in later configurations.
To identify the id 
   it is the one that disappears from the list when you unplug the hub. 

'''
#!/usr/bin/env python3
"""
Robust USB hub lister using pyudev.

Install pyudev if you haven't:
    pip install pyudev

Run on Linux. This will print all USB devices of devtype 'usb_device' and
highlight those that report class 0x09 (hub class).
"""
import sys
import platform

system = platform.system()

# ------------------------------
#  Linux Section (pyudev)
# ------------------------------
if system == "Linux":
    import pyudev

    def decode_attr(device, name):
        """Safely decode a sysfs attribute (bytes) to str, or return None."""
        val = device.attributes.get(name)
        if val is None:
            return None
        try:
            return val.decode('utf-8', errors='replace').strip()
        except Exception:
            return str(val)

    def list_usb_hubs_linux():
        ctx = pyudev.Context()
        hubs = []
        for dev in ctx.list_devices(subsystem='usb', DEVTYPE='usb_device'):
            bdevice_class = decode_attr(dev, 'bDeviceClass')
            udev_class = dev.get('ID_USB_CLASS') or dev.get('ID_USB_CLASS_FROM_DATABASE')

            vid = dev.get('ID_VENDOR_ID') or dev.get('idVendor') or decode_attr(dev, 'idVendor')
            pid = dev.get('ID_MODEL_ID') or dev.get('idProduct') or decode_attr(dev, 'idProduct')
            name = dev.get('ID_MODEL_FROM_DATABASE') or dev.get('ID_MODEL') or dev.get('PRODUCT') or dev.sys_name
            vendor_name = dev.get('ID_VENDOR_FROM_DATABASE') or dev.get('ID_VENDOR') or dev.get('VENDOR')
            sys_name = dev.sys_name
            sys_path = dev.sys_path

            is_hub = False
            # Normalize class like "09" / "0x09"
            if bdevice_class:
                s = bdevice_class.lower().replace('0x', '').zfill(2)
                if s == '09':
                    is_hub = True

            if not is_hub and udev_class:
                try:
                    uc = udev_class.lower().replace('0x', '').zfill(2)
                    if uc == '09':
                        is_hub = True
                except:
                    pass

            hubs.append({
                'sys_name': sys_name,
                'sys_path': sys_path,
                'vid': vid,
                'pid': pid,
                'vendor': vendor_name,
                'model': name,
                'bDeviceClass': bdevice_class,
                'udev_class': udev_class,
                'is_hub': is_hub,
            })

        return hubs


# ------------------------------
#  Unified Wrapper
# ------------------------------
def list_usb_hubs():
    if system == "Linux":
        return list_usb_hubs_linux()
    
    else:
        raise NotImplementedError(f"Unsupported OS: {system}")

# ------------------------------
#  Display Results
# ------------------------------
def print_hubs(hubs):
    if not hubs:
        print("No USB devices found.")
        return

    print("\nDetected USB devices (hub candidates highlighted):\n")
    for h in hubs:
        mark = "HUB" if h['is_hub'] else "----"
        print(f"[{mark}] sys_name: {h['sys_name']}")
        print(f"       sys_path: {h['sys_path']}")
        print(f"       VID:PID: {h['vid']}:{h['pid']}")
        print(f"       vendor: {h['vendor']}")
        print(f"       model: {h['model']}")
        print(f"       bDeviceClass: {h['bDeviceClass']}  udev_class: {h['udev_class']}")
        print("---------------------------------------------------------")

    print("\nNotes:")
    print(" - Entries marked [HUB] are USB hubs.")
    print(" - External hubs usually have non-Linux Foundation vendor names.")
    print(" - Paste your output if you want help identifying the external hub.")

# ------------------------------
#  Main
# ------------------------------
if __name__ == "__main__":
    hubs = list_usb_hubs()
    print_hubs(hubs)
