# Changelog

## 0.2.0

- **One finish for the whole family.** Choosing OBSIDIAN or AURORA here switches every Paragon app, open ones included (within a couple of seconds), and every app starts in the family's choice.
- In AURORA, group titles and selected rows are dark and readable (they were white on the light finish).

## 0.1.1

- Knows the whole family: PR7-DU, PR7-CB, PR7-IS, PR7-PM and PR7-SM are in its list of family apps (their permanent install IDs), so the family links find them when they're installed.

## 0.1.0

- First release: instant file search, the way Everything works. Results appear as you type, across every file on
  your drives. The index service reads each NTFS drive's own index (the MFT) in seconds and keeps up through the
  change journal, so new, renamed and deleted files show up within a second. Windows asks for permission once,
  when the service is installed; drives without an index are walked. Search syntax: wildcards, `|`, `!`, paths,
  `ext:`, `size:`, `dm:`, `file:`, `folder:`, and a filter list (Audio, Document, Picture, Video ...). Enter opens,
  Ctrl+Enter opens the folder (in PR7-E when installed), copy path or name. Ctrl+Alt+Space brings it up from
  anywhere. Options: the drives, things to leave out, the key, the service. It updates itself like the other family
  apps.
