"""
mDNS-based peer discovery using Zeroconf.
Works on Wi-Fi even when routers block UDP broadcast.
Registers this device as _phantomlink._tcp.local service.
Other devices on same Wi-Fi auto-discover it.

Install: pip install zeroconf
"""

import asyncio
import socket
import json
import logging
from typing import Callable, Optional

from zeroconf import ServiceInfo, Zeroconf, ServiceBrowser, ServiceListener
from zeroconf.asyncio import AsyncZeroconf

logger = logging.getLogger("phantomlink.mdns")

SERVICE_TYPE = "_phantomlink._tcp.local."


class PhantomLinkListener(ServiceListener):
    """Called by Zeroconf when a PhantomLink peer appears/disappears on network."""

    def __init__(self, on_peer_found: Callable, on_peer_lost: Callable,
                 own_device_id: str):
        self._on_found = on_peer_found
        self._on_lost = on_peer_lost
        self._own_id = own_device_id

    def add_service(self, zc: Zeroconf, type_: str, name: str):
        info = zc.get_service_info(type_, name)
        if not info:
            return
        props = {k.decode(): v.decode() for k, v in info.properties.items()}
        peer_id = props.get("device_id", "")
        if peer_id == self._own_id:
            return  # ignore ourselves

        addresses = info.parsed_scoped_addresses()
        if not addresses:
            return

        peer_info = {
            "peer_id": peer_id,
            "name": props.get("name", "Unknown"),
            "fingerprint": props.get("fingerprint", ""),
            "ip": addresses[0],
            "port": info.port,
            "transport": "wifi_mdns",
        }
        logger.info(f"mDNS: discovered {peer_info['name']} at {peer_info['ip']}")
        self._on_found(peer_info)

    def remove_service(self, zc: Zeroconf, type_: str, name: str):
        # Extract peer_id from service name (format: devicename_peerid._phantomlink._tcp.local.)
        peer_id = name.split("_")[-1].replace(f".{SERVICE_TYPE}", "").strip(".")
        logger.info(f"mDNS: peer left: {name}")
        self._on_lost(peer_id)

    def update_service(self, zc: Zeroconf, type_: str, name: str):
        self.add_service(zc, type_, name)


class MDNSDiscovery:
    """
    Registers this device on local network via mDNS/Bonjour.
    Discovers other PhantomLink devices automatically.
    Works across Wi-Fi, Ethernet, and most corporate networks
    where UDP broadcast is blocked.
    """

    def __init__(self, identity, tcp_port: int = 47778):
        self._identity = identity
        self._tcp_port = tcp_port
        self._zc: Optional[AsyncZeroconf] = None
        self._browser: Optional[ServiceBrowser] = None
        self._info: Optional[ServiceInfo] = None

        self.on_peer_found: Optional[Callable] = None
        self.on_peer_lost: Optional[Callable] = None

    async def start(self):
        """Register service + start browsing for peers."""
        self._zc = AsyncZeroconf()

        # Build service properties (TXT records)
        props = {
            "device_id": self._identity.device_id,
            "name": self._identity.name,
            "fingerprint": self._identity.fingerprint,
        }

        # Service name must be unique — use device_id suffix
        service_name = f"{self._identity.name}_{self._identity.device_id[:8]}.{SERVICE_TYPE}"

        # Get local IP
        local_ip = self._get_local_ip()
        ip_bytes = socket.inet_aton(local_ip)

        self._info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[ip_bytes],
            port=self._tcp_port,
            properties=props,
        )

        await self._zc.async_register_service(self._info)
        logger.info(f"mDNS: registered as {service_name} at {local_ip}:{self._tcp_port}")

        # Start browsing
        listener = PhantomLinkListener(
            on_peer_found=self.on_peer_found or (lambda x: None),
            on_peer_lost=self.on_peer_lost or (lambda x: None),
            own_device_id=self._identity.device_id,
        )
        self._browser = ServiceBrowser(
            self._zc.zeroconf, SERVICE_TYPE, listener
        )
        logger.info("mDNS: browsing for peers…")

    async def stop(self):
        if self._zc and self._info:
            await self._zc.async_unregister_service(self._info)
            await self._zc.async_close()

    def _get_local_ip(self) -> str:
        """Best-effort local IP detection."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"
