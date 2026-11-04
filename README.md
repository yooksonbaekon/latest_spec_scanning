# Free 3GPP file monitor

This repository recursively checks `https://www.3gpp.org/ftp/Specs/latest` twice daily and opens a GitHub issue when new files are found.

## Set-up

1. Create a GitHub repository. A public repository gives free standard GitHub-hosted Actions usage. A private repository uses the monthly allowance included with the account.
2. Copy all files, including `.github/workflows/monitor.yml`, into the repository.
3. In **Settings > Actions > General > Workflow permissions**, select **Read and write permissions** and save.
4. Push the files to the default branch.
5. Open **Actions > Monitor 3GPP latest files > Run workflow** once.

The first successful run creates the baseline and intentionally sends no notification. Later runs open an issue only when new file paths are detected.

To receive email notifications, watch the repository or ensure GitHub issue notifications are enabled for your account.

## Schedule

The workflow runs at 06:17 and 16:17 UTC every day. Change the `cron` expression in `.github/workflows/monitor.yml` if needed.

## Files

- `monitor.py`: recursive scanner and comparison logic
- `config.json`: monitored URL and request settings
- `state.json`: persistent baseline, committed by the workflow
- `.github/workflows/monitor.yml`: scheduler, snapshot commit, and issue notification

## Safety behavior

The script does not replace the baseline if the crawl fails or returns zero files. It retries transient failures and limits the number of directories to prevent an accidental runaway crawl.
