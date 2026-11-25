# Changelog

## 0.1.0

- First release: instant file search, the way Everything works. Results appear as you type, across every file on
  your drives. The index service reads each NTFS drive's own index (the MFT) in seconds and keeps up through the
  change journal, so new, renamed and deleted files show up within a second. Windows asks for permission once,
  when the service is installed; drives without an index are walked. Search syntax: wildcards, `|`, `!`, paths,
  `ext:`, `size:`, `dm:`, `file:`, `folder:`, and a filter list (Audio, Document, Picture, Video ...). Enter opens,
  Ctrl+Enter opens the folder (in PR7-E when installed), copy path or name. Ctrl+Alt+Space brings it up from
  anywhere. Options: the drives, things to leave out, the key, the service. It updates itself like the other family
  apps.
