import platform
import sys

TARGET_VID = "214b"   # <-- your hub VID (lowercase)
TARGET_PID = "7250"   # <-- your hub PID (lowercase)

system = platform.system()

# -------------------- LINUX SECTION --------------------
if system == "Linux":
    import pyudev

    def find_hub_linux():
        ctx = pyudev.Context()
        for dev in ctx.list_devices(subsystem="usb", DEVTYPE="usb_device"):
            vid = dev.get('ID_VENDOR_ID') or dev.get('idVendor')
            pid = dev.get('ID_MODEL_ID')  or dev.get('idProduct')

            if vid and pid:
                if vid.lower() == TARGET_VID and pid.lower() == TARGET_PID:
                    print("FOUND EXTERNAL HUB (Linux)")
                    print(f"sys_name: {dev.sys_name}")
                    print(f"sys_path: {dev.sys_path}")

                    # -------------------------------
                    # GET UNDERLYING CHILDREN PORT IDs
                    # -------------------------------
                    print("\nPorts under this hub:")
                    hub_prefix = dev.sys_name + "."

                    ports = []
                    for child in dev.children:
                        # Only include children that belong to this hub (e.g., 3-4.1, 3-4.2, etc.)
                        if child.sys_name.startswith(hub_prefix):
                            ports.append(child.sys_name)

                    ports.sort()
                    for p in ports:
                        print(" PORT", p)

                    return True

        print("External hub NOT found on Linux.")
        return False

    find_hub_linux()

