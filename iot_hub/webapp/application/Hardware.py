"""
Hardware Class
"""
import socket
import urllib.request
import platform
import sys
import pkg_resources
import psutil
from datetime import datetime
import subprocess
from kafka import KafkaConsumer
from json import loads


class Hardware:
    def __init__(self):
        self.hostname = socket.getfqdn()

    def get_picow_ip(self, topic: str, servers: list):
        consumer = KafkaConsumer(
            topic, bootstrap_servers=servers, auto_offset_reset="latest", group_id=None
        )
        for msg in consumer:
            data = loads(msg.value.decode("utf-8"))
            ip = data["picow"]["local_ip"]
            break

        consumer.close()
        return ip

    def check_port(self, host, port):
        a_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        location = (host, port)
        check = a_socket.connect_ex(location)
        a_socket.close()
        return True if check == 0 else False

    def get_cpu_usage(self):
        return psutil.cpu_percent(interval=1)

    def get_mem_usage(self):
        return psutil.virtual_memory().percent

    def get_disk_usage(self):
        return psutil.disk_usage("/").percent

    def check_if_host_alive(self, host: str) -> bool:
        return (
            True
            if subprocess.run(["ping", "-c", "1", "-w", "1", host]).returncode == 0
            else False
        )

    def get_local_ip(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # doesn't even have to be reachable
            s.connect(("192.255.255.255", 1))
            IP = s.getsockname()[0]
        except:
            IP = "127.0.0.1"
        finally:
            s.close()
        return IP

    def get_external_ip(self):
        return urllib.request.urlopen("https://ident.me").read().decode("utf8")

    def get_pkgs(self):
        installed_packages = pkg_resources.working_set
        installed_packages_list = sorted(
            ["%s==%s" % (i.key, i.version) for i in installed_packages]
        )
        return installed_packages_list

    def get_size(self, bytes, suffix="B"):
        """
        Scale bytes to its proper format
        e.g:
            1253656 => '1.20MB'
            1253656678 => '1.17GB'
        """
        factor = 1024
        for unit in ["", "K", "M", "G", "T", "P"]:
            if bytes < factor:
                return f"{bytes:.2f}{unit}{suffix}"
            bytes /= factor

    def get_boot_time(self):
        boot_time_timestamp = psutil.boot_time()
        bt = datetime.fromtimestamp(boot_time_timestamp)
        return f"{bt.year}/{bt.month}/{bt.day} {bt.hour}:{bt.minute}:{bt.second}"

    def get_disk_partitions(self) -> str:
        partitions = psutil.disk_partitions()
        result = ""
        for partition in partitions:
            result += f"=== Device: {partition.device} ===\n"
            result += f"  Mountpoint: {partition.mountpoint}\n"
            result += f"  File system type: {partition.fstype}\n"
            try:
                partition_usage = psutil.disk_usage(partition.mountpoint)
            except PermissionError:
                # this can be catched due to the disk that
                # isn't ready
                continue
            result += f"  Total Size: {self.get_size(partition_usage.total)}\n"
            result += f"  Used: {self.get_size(partition_usage.used)}\n"
            result += f"  Free: {self.get_size(partition_usage.free)}\n"
            result += f"  Percentage: {partition_usage.percent}%"
        return result

    def get_network(self) -> str:
        if_addrs = psutil.net_if_addrs()
        result = ""
        for interface_name, interface_addresses in if_addrs.items():
            for address in interface_addresses:
                result += f"=== Interface: {interface_name} ===\n"
                if str(address.family) == "AddressFamily.AF_INET":
                    result += f"  IP Address: {address.address}\n"
                    result += f"  Netmask: {address.netmask}\n"
                    result += f"  Broadcast IP: {address.broadcast}\n"
                elif str(address.family) == "AddressFamily.AF_PACKET":
                    result += f"  MAC Address: {address.address}\n"
                    result += f"  Netmask: {address.netmask}\n"
                    result += f"  Broadcast MAC: {address.broadcast}\n"
        return result

    def get_all_network(self) -> dict:
        net_io = psutil.net_io_counters()
        return {
            "local_ip": self.get_local_ip(),
            "external_ip": self.get_external_ip(),
            "hostname": self.hostname,
            "network": self.get_network(),
            "total_bytes_sent": self.get_size(net_io.bytes_sent),
            "total_bytes_received": self.get_size(net_io.bytes_recv),
        }

    def get_all_hardware(self) -> dict:
        uname = platform.uname()
        cpufreq = psutil.cpu_freq()
        svmem = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk_io = psutil.disk_io_counters()

        return {
            "system": uname.system,
            "machine": uname.machine,
            "node": uname.node,
            "release": uname.release,
            "version": uname.version,
            "processor": uname.processor,
            "boot_time": self.get_boot_time(),
            "physical_cpu_cores": psutil.cpu_count(logical=False),
            "total_cpu_cores": psutil.cpu_count(logical=True),
            "current_cpu_freq": f"{cpufreq.current:.2f}Mhz",
            "max_cpu_freq": f"{cpufreq.max:.2f}Mhz",
            "min_cpu_freq": f"{cpufreq.min:.2f}Mhz",
            "cpu_util_percent": "\n".join(
                [
                    f"Core {i}: {percentage}%"
                    for i, percentage in enumerate(
                        psutil.cpu_percent(percpu=True, interval=1)
                    )
                ]
            ),
            "total_cpu_usage": f"{psutil.cpu_percent()}%",
            "total_mem": self.get_size(svmem.total),
            "available_mem": self.get_size(svmem.available),
            "used_mem": self.get_size(svmem.used),
            "mem_percent": svmem.percent,
            "mem_swap_total": self.get_size(swap.total),
            "mem_swap_free": self.get_size(swap.free),
            "mem_swap_used": self.get_size(swap.used),
            "mem_swap_percent": swap.percent,
            "disk_partitions": self.get_disk_partitions(),
            "disk_total_read": self.get_size(disk_io.read_bytes),
            "disk_total_write": self.get_size(disk_io.write_bytes),
        }

    def get_all_python(self) -> dict:
        return {
            "python_build": platform.python_build(),
            "python_version": platform.python_version(),
            "python_compiler": platform.python_compiler(),
            "python_branch": platform.python_branch(),
            "python_implementation": platform.python_implementation(),
            "python_revision": platform.python_revision(),
            "java_version": platform.java_ver(),
            "executable": sys.executable,
            "sys.argv": sys.argv,
            "base_prefix": sys.base_prefix,
            "pip_pkgs": "\n".join(self.get_pkgs()),
        }
