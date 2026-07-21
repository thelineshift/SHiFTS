# LineShift Discord Bot
Command-queue automation bot for the TheLineShift Discord server.
Polls bot_commands.json in the main repo every 60s and executes queued
actions (rename channels, set topics, permissions, pin posts, create/delete
channels). Logs results to bot_state.json.
Secrets live in Railway env vars only — never in this repo.
