"""
Run this ON THE PI whenever enrollment isn't working. It tests each hop in the
enrollment pipeline separately, so instead of "enrollment doesn't work" you get
exactly which step failed.

Usage: python3 diagnose_enrollment.py [--backend-ip 192.168.1.72]
"""

import argparse
import base64
import sys
import subprocess

import cv2
import numpy as np


def check(label, ok, detail=""):
    mark = "✓" if ok else "✗"
    print(f"[{mark}] {label}" + (f" - {detail}" if detail else ""))
    return ok


def make_test_photo():
    """A real-looking (textured, non-blurry) synthetic image so quality checks
    have something realistic to grade - NOT a real face, so face detection is
    expected to legitimately reject it. This test is about the PIPELINE
    working, not about actually recognizing a face."""
    np.random.seed(0)
    img = (np.random.rand(400, 400, 3) * 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    return base64.b64encode(buf).decode()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend-ip", default="192.168.1.72", help="Your Windows backend's IP")
    parser.add_argument("--backend-port", default=8000)
    parser.add_argument("--fp-port", default=8095)
    args = parser.parse_args()

    print("=" * 60)
    print("ENROLLMENT PIPELINE DIAGNOSTIC")
    print("=" * 60)

    # 1. Is face_recognition/dlib actually importable on this Pi?
    try:
        import face_recognition
        check("face_recognition/dlib import", True)
    except ImportError as e:
        check("face_recognition/dlib import", False, str(e))
        print("\n>>> FIX: pip install face-recognition --extra-index-url https://www.piwheels.org/simple")
        sys.exit(1)

    # 2. Does the encoding logic itself work, in-process (no HTTP involved yet)?
    try:
        from face_processor import quality_check, base64_to_cv2
        photo_b64 = make_test_photo()
        image = base64_to_cv2(photo_b64)
        ok, msg = quality_check(image)
        check("Local encoding logic runs without crashing", True, f"quality_check result: {msg}")
    except Exception as e:
        check("Local encoding logic runs without crashing", False, str(e))
        print("\n>>> This is a real bug in face_processor.py itself - the code crashed, not just rejected a photo.")
        sys.exit(1)

    # 3. Is face_processor.py's HTTP server actually running and reachable locally?
    import urllib.request
    fp_url = f"http://127.0.0.1:{args.fp_port}/health"
    try:
        with urllib.request.urlopen(fp_url, timeout=5) as resp:
            check("face_processor.py responds on localhost", resp.status == 200, fp_url)
    except Exception as e:
        check("face_processor.py responds on localhost", False, str(e))
        print(f"\n>>> FIX: face_processor.py isn't running. Start it: python3 face_processor.py --port {args.fp_port}")
        print(">>> If start_edge.sh is supposed to be running it, check face_processor.log for a crash.")
        sys.exit(1)

    # 4. Is this Pi's face_processor reachable from OUTSIDE (i.e. will the
    #    Windows backend actually be able to reach it)? Check what IP this
    #    Pi is actually on, since that's what needs to be in the backend's
    #    FACE_PROCESSOR_URL.
    try:
        result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        pi_ips = result.stdout.strip().split()
        check("This Pi's actual LAN IP(s)", True, ", ".join(pi_ips) if pi_ips else "none found")
        print(f"\n    >>> The backend's FACE_PROCESSOR_URL environment variable MUST point to one")
        print(f"    >>> of these IPs, e.g.: http://{pi_ips[0] if pi_ips else '<PI_IP>'}:{args.fp_port}")
    except Exception as e:
        check("This Pi's actual LAN IP(s)", False, str(e))

    # 5. Can this Pi reach the backend (checks the network path both ways matter)?
    backend_url = f"http://{args.backend_ip}:{args.backend_port}/"
    try:
        with urllib.request.urlopen(backend_url, timeout=5) as resp:
            check(f"Backend reachable from this Pi ({backend_url})", resp.status == 200)
    except Exception as e:
        check(f"Backend reachable from this Pi ({backend_url})", False, str(e))
        print("\n>>> If this fails: check the backend is actually running, the IP is correct,")
        print(">>> and no firewall on the Windows machine is blocking incoming connections on this port.")

    print("\n" + "=" * 60)
    print("ONE MORE MANUAL CHECK NEEDED - run this ON THE WINDOWS MACHINE")
    print("(this script can't test it from here, since it's the reverse direction):")
    print("=" * 60)
    if 'pi_ips' in dir() and pi_ips:
        print(f'  PowerShell:  Invoke-WebRequest http://{pi_ips[0]}:{args.fp_port}/health')
        print(f'  or:          curl http://{pi_ips[0]}:{args.fp_port}/health')
    print("If THAT fails while everything above passed, the Pi's firewall is blocking")
    print(f"incoming connections on port {args.fp_port} - run on the Pi:")
    print(f"  sudo ufw allow {args.fp_port}")


if __name__ == "__main__":
    main()
