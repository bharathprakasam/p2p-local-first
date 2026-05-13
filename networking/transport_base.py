"""
TransportBase — abstract interface all transports must implement.
Wi-Fi TCP and Bluetooth both implement this so the rest of the app
is transport-agnostic. Messages are identical regardless of transport.
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional


class TransportBase(ABC):
    """
    Every transport must:
      - discover peers on its medium
      - connect to a specific peer
      - send/receive length-prefixed JSON messages (same protocol as TCP)
      - report its transport type so UI can show Wi-Fi / BT icon
    """

    def __init__(self):
        # Callbacks set by P2PNode
        self.on_peer_discovered: Optional[Callable] = None
        self.on_peer_lost: Optional[Callable] = None
        self.on_message: Optional[Callable] = None
        self.on_connected: Optional[Callable] = None
        self.on_disconnected: Optional[Callable] = None

    @property
    @abstractmethod
    def transport_type(self) -> str:
        """Return 'wifi' | 'bluetooth_classic' | 'ble'"""

    @abstractmethod
    async def start(self):
        """Start discovery + listening."""

    @abstractmethod
    async def stop(self):
        """Graceful shutdown."""

    @abstractmethod
    async def connect(self, address: str, **kwargs) -> bool:
        """Connect to peer by address. Returns True on success."""

    @abstractmethod
    async def send(self, peer_address: str, data: dict) -> bool:
        """Send JSON dict to connected peer."""

    @abstractmethod
    def get_discovered_peers(self) -> list:
        """Return list of discovered peer dicts."""
