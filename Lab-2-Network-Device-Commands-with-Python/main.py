import getpass
import logging
from pathlib import Path

from netmiko import ConnectHandler
from netmiko.exceptions import AuthenticationException, NetMikoTimeoutException
from ntc_templates.parse import ParsingException, parse_output
from paramiko.ssh_exception import SSHException


PROJECT_ROOT = Path(__file__).resolve().parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
REPORT_DIR = PROJECT_ROOT / "data" / "reports"
LOG_PATH = PROJECT_ROOT / "logs" / "lab.log"
COMMANDS = ("show version", "show ip interface brief", "show inventory")


def configure_logging():
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _first_value(rows, *keys):
    for row in rows:
        for key in keys:
            value = row.get(key)
            if value:
                if isinstance(value, (list, tuple)):
                    return ", ".join(str(item) for item in value)
                return str(value)
    return "N/A"


def _build_report(parsed_outputs):
    version_rows = parsed_outputs.get("show version", [])
    inventory_rows = parsed_outputs.get("show inventory", [])
    interface_rows = parsed_outputs.get("show ip interface brief", [])

    hostname = _first_value(version_rows, "hostname")
    model = _first_value(version_rows, "hardware", "model")
    if model == "N/A":
        model = _first_value(inventory_rows, "pid", "model")
    version = _first_value(version_rows, "version", "os")

    lines = [
        "Cisco Device Summary",
        f"Hostname: {hostname}",
        f"Model: {model}",
        f"IOS version: {version}",
        f"Interfaces parsed: {len(interface_rows)}",
        "Interface states:",
    ]
    if interface_rows:
        for row in interface_rows:
            interface = (
                row.get("intf")
                or row.get("interface")
                or row.get("port")
                or "Unknown"
            )
            status = row.get("status") or "unknown"
            protocol = row.get("proto") or row.get("protocol") or "unknown"
            lines.append(f"  {interface}: {status}/{protocol}")
    else:
        lines.append("  No parsed interface data available.")
    return "\n".join(lines) + "\n"


def main():
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    host = input("Enter device IP or hostname: ").strip()
    username = input("Enter username: ").strip()
    password = getpass.getpass("Enter password: ")
    if not host or not username or not password:
        logging.error("Host, username, and password are required.")
        print("Host, username, and password are required.")
        return 1
    logging.info("[CREDENTIALS_COLLECTED]")

    try:
        connection = ConnectHandler(
            device_type="cisco_ios",
            host=host,
            username=username,
            password=password,
            timeout=10,
        )
    except AuthenticationException:
        logging.error("[CONNECT_FAIL] Authentication failed.")
        print("Connection failed: authentication was rejected.")
        return 1
    except NetMikoTimeoutException:
        logging.error("[CONNECT_FAIL] Connection timed out.")
        print("Connection failed: the device did not respond before the timeout.")
        return 1
    except SSHException:
        logging.error("[CONNECT_FAIL] SSH negotiation failed.")
        print("Connection failed: SSH negotiation was unsuccessful.")
        return 1
    except Exception as exc:
        logging.error("[CONNECT_FAIL] Unexpected error (%s).", type(exc).__name__)
        print(f"Connection failed: {type(exc).__name__}.")
        return 1

    logging.info("[CONNECT_OK] Connected to %s.", host)
    print(f"Connected to {host}.")
    parsed_outputs = {}
    had_errors = False

    try:
        for command in COMMANDS:
            try:
                raw_output = connection.send_command(command)
                logging.info("CMD_RUN:%s", command)
                output_path = RAW_DIR / f"{command.replace(' ', '_')}.txt"
                output_path.write_text(raw_output + "\n", encoding="utf-8")
            except Exception as exc:
                had_errors = True
                logging.error(
                    "CMD_FAIL:%s (%s)", command, type(exc).__name__
                )
                print(f"Command failed ({command}): {type(exc).__name__}.")
                continue

            try:
                parsed = parse_output(
                    platform="cisco_ios",
                    command=command,
                    data=raw_output,
                )
            except (ParsingException, ImportError) as exc:
                had_errors = True
                logging.error("PARSE_FAIL:%s (%s)", command, type(exc).__name__)
                print(f"Could not parse {command}: {type(exc).__name__}.")
                continue

            if not parsed or not all(isinstance(row, dict) for row in parsed):
                had_errors = True
                logging.error(
                    "PARSE_FAIL:%s (empty or invalid structured data)", command
                )
                print(f"Could not parse {command}: no structured data was returned.")
                continue

            parsed_outputs[command] = parsed
            logging.info("PARSE_OK:%s", command)

        report = _build_report(parsed_outputs)
        print(f"\n{report}", end="")
        report_path = REPORT_DIR / "device_summary.txt"
        try:
            report_path.write_text(report, encoding="utf-8")
        except OSError as exc:
            had_errors = True
            logging.error("[REPORT_FAIL] Could not save report (%s).", type(exc).__name__)
            print(f"Could not save report: {type(exc).__name__}.")
        else:
            logging.info("[REPORT_SAVED] %s", report_path)
    finally:
        try:
            connection.disconnect()
            logging.info("SSH session closed.")
        except Exception as exc:
            had_errors = True
            logging.error("DISCONNECT_FAIL (%s).", type(exc).__name__)
            print(f"Could not close the SSH session cleanly: {type(exc).__name__}.")
    return 1 if had_errors else 0


if __name__ == "__main__":
    configure_logging()
    logging.info("[LAB2-START]")
    try:
        raise SystemExit(main())
    finally:
        logging.info("[LAB2-END]")
