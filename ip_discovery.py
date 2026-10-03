"""
IP Discovery: Automatically find the backend even when Pi IP changes.

Instead of hardcoding an IP like 192.168.29.83, we use mDNS (Multicast DNS)
to discover the backend by hostname. This survives IP changes, WiFi changes, etc.

How it works:
  1. Backend (Windows) machine has a hostname (e.g., "DESKTOP-ABC123")
  2. Pi queries for that hostname via mDNS - gets the current IP automatically
  3. If mDNS fails, falls back to hardcoded IP for safety

Install on Pi: pip install zeroconf
"""

import socket
import time
from typing import Optional

def get_backend_url(backend_hostname: str = None, fallback_url: str = None) -> str:
    """
    Auto-discover backend URL via mDNS, with fallback to hardcoded IP.
    
    Args:
        backend_hostname: Hostname of the backend machine (e.g., "DESKTOP-XYZ")
        fallback_url: Hardcoded URL as fallback (e.g., "http://192.168.29.83:8000")
    
    Returns:
        Backend URL string (e.g., "http://192.168.29.83:8000")
    """
    # Try mDNS discovery first
    if backend_hostname:
        print(f"[IP_DISCOVERY] Attempting mDNS lookup for {backend_hostname}.local...")
        try:
            from zeroconf import Zeroconf, ServiceBrowser
            import socket
            
            # Try simple hostname resolution first (works if in same LAN)
            try:
                ip = socket.gethostbyname(f"{backend_hostname}.local")
                url = f"http://{ip}:8000"
                print(f"[IP_DISCOVERY] ✓ Found backend at {url}")
                return url
            except socket.gaierror:
                pass
            
            # Try full mDNS discovery
            zc = Zeroconf()
            for service_type in ["_http._tcp.local.", "_services._dns-sd._udp.local."]:
                try:
                    # Search for the service
                    info = zc.get_service_info(service_type, f"{backend_hostname}._http._tcp.local.")
                    if info:
                        ip = socket.inet_ntoa(info.addresses[0])
                        port = info.port
                        url = f"http://{ip}:{port}"
                        print(f"[IP_DISCOVERY] ✓ Found backend at {url}")
                        zc.close()
                        return url
                except:
                    pass
            zc.close()
        except ImportError:
            print("[IP_DISCOVERY] zeroconf not installed - skipping mDNS lookup")
        except Exception as e:
            print(f"[IP_DISCOVERY] mDNS lookup failed: {e}")
    
    # Fallback to hardcoded URL
    if fallback_url:
        print(f"[IP_DISCOVERY] Using fallback URL: {fallback_url}")
        return fallback_url
    
    # Last resort - no backend found
    print("[IP_DISCOVERY] ✗ Could not discover backend URL!")
    return None


def ping_backend(backend_url: str, timeout: int = 5) -> bool:
    """
    Test if backend is reachable.
    
    Args:
        backend_url: URL to test
        timeout: Timeout in seconds
    
    Returns:
        True if reachable, False otherwise
    """
    try:
        import requests
        resp = requests.get(f"{backend_url}/api/devices/ping", timeout=timeout)
        return resp.status_code == 200
    except:
        return False


def ensure_backend_reachable(backend_url: str, max_retries: int = 10) -> bool:
    """
    Wait for backend to become reachable (with retries).
    
    Args:
        backend_url: URL to test
        max_retries: How many times to retry
    
    Returns:
        True if backend became reachable, False if gave up
    """
    print(f"[IP_DISCOVERY] Waiting for backend at {backend_url}...")
    for attempt in range(max_retries):
        if ping_backend(backend_url):
            print(f"[IP_DISCOVERY] ✓ Backend is reachable!")
            return True
        
        wait_sec = 3
        print(f"[IP_DISCOVERY] Attempt {attempt+1}/{max_retries} - retrying in {wait_sec}s...")
        time.sleep(wait_sec)
    
    print(f"[IP_DISCOVERY] ✗ Backend never became reachable")
    return False
