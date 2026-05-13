"""
fix_network.py — Run this on BOTH machines to diagnose and fix PhantomLink discovery.
Usage:  python fix_network.py
"""
import socket, sys, platform, subprocess, json, time

UDP_PORT = 47777
TCP_PORT = 47778
OS = platform.system()


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80)); ip = s.getsockname()[0]; s.close(); return ip
    except: return "unknown"


def subnet_broadcast(ip):
    p = ip.split(".")
    return f"{p[0]}.{p[1]}.{p[2]}.255" if len(p) == 4 else "255.255.255.255"


def section(title):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print('─'*50)


# ── 1. Network info ───────────────────────────────────────────────────────────
section("1. Network Info")
ip = local_ip()
bcast = subnet_broadcast(ip)
print(f"  Local IP         : {ip}")
print(f"  Subnet broadcast : {bcast}")
print(f"  OS               : {OS}")
print()
print("  ⚠  BOTH devices must be on the same Wi-Fi/LAN subnet.")
print(f"     Check that both IPs start with the same prefix, e.g. 192.168.1.x")


# ── 2. UDP send test ──────────────────────────────────────────────────────────
section("2. UDP Broadcast Send Test")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.sendto(b'{"type":"test"}', (bcast, UDP_PORT))
    s.sendto(b'{"type":"test"}', ("255.255.255.255", UDP_PORT))
    s.close()
    print(f"  ✓ UDP broadcast to {bcast}:{UDP_PORT} — OK")
    print(f"  ✓ UDP broadcast to 255.255.255.255:{UDP_PORT} — OK")
except Exception as e:
    print(f"  ✗ UDP send failed: {e}")


# ── 3. UDP receive test ───────────────────────────────────────────────────────
section("3. UDP Listen Test (2 second timeout)")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("", UDP_PORT))
    s.settimeout(2)
    try:
        data, addr = s.recvfrom(4096)
        print(f"  ✓ Received UDP from {addr}: {data[:60]}")
    except socket.timeout:
        print(f"  ✗ No UDP packets received on port {UDP_PORT} in 2s")
        print("    → Run PhantomLink on other device first, then run this again")
        print("    → Or: firewall is blocking incoming UDP 47777")
    s.close()
except Exception as e:
    print(f"  ✗ Cannot bind UDP {UDP_PORT}: {e}")
    print("    → Port already in use, or firewall blocking bind")


# ── 4. TCP port test ──────────────────────────────────────────────────────────
section("4. TCP Port Test")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("0.0.0.0", TCP_PORT))
    s.close()
    print(f"  ✓ TCP port {TCP_PORT} is free and bindable")
except Exception as e:
    print(f"  ✗ TCP port {TCP_PORT} issue: {e}")


# ── 5. Firewall fix instructions ──────────────────────────────────────────────
section("5. Firewall Fix Commands (run as admin/root)")
if OS == "Windows":
    print("""
  Run in PowerShell (Admin):

  netsh advfirewall firewall add rule name="PhantomLink UDP" `
    protocol=UDP dir=in localport=47777 action=allow

  netsh advfirewall firewall add rule name="PhantomLink TCP" `
    protocol=TCP dir=in localport=47778 action=allow

  Or via GUI:
    Windows Defender Firewall → Advanced Settings
    → Inbound Rules → New Rule → Port → UDP 47777, TCP 47778 → Allow
""")
elif OS == "Linux":
    print("""
  Run in terminal:

  sudo ufw allow 47777/udp
  sudo ufw allow 47778/tcp
  sudo ufw reload

  If using firewalld:
  sudo firewall-cmd --add-port=47777/udp --permanent
  sudo firewall-cmd --add-port=47778/tcp --permanent
  sudo firewall-cmd --reload
""")
elif OS == "Darwin":
    print("""
  macOS: System Settings → Network → Firewall
  Click "Options..." → Add PhantomLink → Allow incoming
  Or disable firewall temporarily to test.

  Note: macOS blocks incoming TCP by default for unsigned apps.
""")


# ── 6. mDNS dependency check ──────────────────────────────────────────────────
section("6. mDNS (zeroconf) — Backup Discovery")
try:
    import zeroconf
    print(f"  ✓ zeroconf installed: {zeroconf.__version__}")
    print("    mDNS will run as fallback when UDP broadcast is blocked")
except ImportError:
    print("  ✗ zeroconf not installed")
    print("    Install: pip install zeroconf")
    print("    mDNS works even when routers block UDP 255.255.255.255")


# ── 7. Summary ────────────────────────────────────────────────────────────────
section("7. Summary — Most Common Causes of Asymmetric Discovery")
print("""
  A sees B but B doesn't see A — root causes (in order of likelihood):

  1. FIREWALL on B blocks incoming UDP 47777
     Fix: run the firewall commands in section 5 above on BOTH machines

  2. Different subnets / VLANs
     Fix: confirm both IPs have same prefix (e.g. 192.168.1.x)
     Router may isolate Wi-Fi clients — disable "AP isolation" in router settings

  3. zeroconf not installed (mDNS fallback missing)
     Fix: pip install zeroconf  (on both machines)

  4. router blocks 255.255.255.255 broadcast (very common)
     Already fixed in updated node.py: now also sends to subnet broadcast (192.168.x.255)
""")
