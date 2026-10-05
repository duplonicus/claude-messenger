#!/usr/bin/env bash
# Show a fresh WhatsApp pairing QR. On the phone: Settings -> Linked devices
# -> Link a device. Ctrl-C once it says "Successfully connected".
systemctl --user restart whatsapp-bridge
exec journalctl --user -u whatsapp-bridge -f -o cat --since "-2s"
