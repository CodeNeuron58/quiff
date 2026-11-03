# Notepad

How Notepad (Windows 11 store app) exposes itself and how to drive it.

## Structure

- The document is the row `document "Text editor"` — write text by typing
  into it (the engine verifies the value landed; do not retype afterwards).
- Menu items: File, Edit, View. The tab strip shows one `tab item` per open
  document, named `<file>. <Modified|Unmodified>.`

## Flows

- **Write text**: type into the document row. SetValue commits instantly.
- **Save an existing file**: press Ctrl+S — it saves in place silently.
- **Save to a path (a new file, or a new name)**: use the `save_as` action
  with the FULL destination path (folder + name). It is one step: the engine
  drives the Save-As dialog itself and checks the file landed.
- Only if a Save-As dialog is already open: type the FULL path into its
  `File name` box, then press Enter. Typing only a file name saves to
  whatever folder the dialog defaults to.

## Quirks

- Notepad restores session tabs on start: blank `Untitled` tabs from earlier
  runs may be present. Ignore them unless they hold the target document.
- A title starting with `*` means unsaved changes.
- The Save-As dialog may confirm overwrites ("Confirm Save As") — answer
  Yes only when the task intends to replace that exact file.
