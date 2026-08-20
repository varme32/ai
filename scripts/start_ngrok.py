import asyncio
import sys
import ngrok

authtoken = "3I7saaGpo7nk79KIoTTKcgDSCwy_54y1zzmdGYGuabRFoTDdj"
domain = "smoked-nineteen-exact.ngrok-free.dev"
port = 8000

async def main():
    print(f"Connecting permanent tunnel to {domain} -> http://127.0.0.1:{port} ...")
    session = await (
        ngrok.SessionBuilder()
        .authtoken(authtoken)
        .connect()
    )

    listener = await (
        session.http_endpoint()
        .domain(domain)
        .listen_and_forward(f"http://127.0.0.1:{port}")
    )

    print(f"\n[OK] Permanent ngrok Tunnel is ONLINE!")
    print(f"URL: {listener.url()}")
    print(f"Forwarding to http://127.0.0.1:{port}")
    print("\nKeep this running. Press Ctrl+C to stop.\n")

    try:
        while True:
            await asyncio.sleep(1)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\nClosing tunnel...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Tunnel closed.")
