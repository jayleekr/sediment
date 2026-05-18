# launchd plists for Sediment

Two plists handle scheduled ingest + memory consolidation. Install with:

```bash
cp infra/launchd/com.hypeproof.sediment.*.plist ~/Library/LaunchAgents/
launchctl load -w ~/Library/LaunchAgents/com.hypeproof.sediment.daily-ingest.plist
launchctl load -w ~/Library/LaunchAgents/com.hypeproof.sediment.dream.plist
```

To verify:
```bash
launchctl list | grep curator
```

To remove:
```bash
launchctl unload ~/Library/LaunchAgents/com.hypeproof.sediment.daily-ingest.plist
rm ~/Library/LaunchAgents/com.hypeproof.sediment.daily-ingest.plist
```

Logs:
- `/tmp/curator-daily-ingest.log` / `.err`
- `/tmp/curator-dream.log` / `.err`

> If you move the repo, edit the absolute paths in the plists.
