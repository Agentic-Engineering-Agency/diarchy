## Summary

<!-- What changes and why. Link the issue. -->

## Tests run

<!-- Paste the commands and the result. -->

- [ ] `python3 -m unittest discover -s tests -p 'test_*.py'`
- [ ] `swift test --package-path app` (if `app/` changed)
- [ ] `bash -n` on each changed script

## Leak gate

- [ ] `scripts/check-public-tree` exits 0
- [ ] I added no real IP, MAC address, SSID, host name, user name or home path

## Hardware tested

<!-- Mac model and macOS. Host model, GPU and Omarchy version. Or write "none". -->
