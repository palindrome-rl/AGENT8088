import asyncio
import logging


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    from agent8088.gateway.runner import build_runner
    runner = build_runner()
    if not runner.adapters:
        logging.error("No messaging platforms enabled. Set one of slack_enabled, whatsapp_enabled, discord_enabled, email_enabled, or telegram_enabled to 1 in config.txt (or run: agent8088 --gateway-setup).")
        return
    try:
        asyncio.run(runner.run())
    except KeyboardInterrupt:
        logging.info("Gateway stopped.")


if __name__ == "__main__":
    main()