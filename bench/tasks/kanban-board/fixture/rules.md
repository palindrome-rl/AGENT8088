# Team Board: rules

A Kanban board for a small team, like a simple Trello or Jira board.

## Columns
To Do, In Progress, In Review, Done (in this order, left to right).

## Cards
A card has: a title, an assignee (one of the people in `seed.json`), a priority (Low, Medium or High), an optional due date (YYYY-MM-DD) and a checklist (a list of small steps, each ticked or not).
Every card also remembers how many days it has been in its current column (`daysInColumn`).

## First load
When the page is opened and nothing has been saved yet, the board shows the cards from `seed.json` in their columns.
After that, the board is saved in the browser and is still there after a reload.

## The team rules
1. **Per-person limit.** A person may have at most 3 cards in **In Progress** at the same time. A move that would give someone a fourth is refused, the card stays where it was, and a message names the person (for example "Ben already has 3 cards in progress").
2. **Done needs a finished checklist.** A card can move to **Done** only if every checklist item is ticked. Otherwise the move is refused and a message says the checklist is not finished. A card with an empty checklist can move to Done.
3. **Review reminder.** There is an **Advance day** button. Each press adds 1 to `daysInColumn` of every card. A card that has been in **In Review** for 2 or more days shows the label **Overdue review**, and overdue cards are listed first in their column.
4. **Moving a card** sets its `daysInColumn` back to 0.
5. **Export and import.** *Export* downloads the whole board as a JSON file in the format of `export_example.json`. *Import* loads such a file and replaces the board. If the file is broken, the import is refused as a whole: the board stays exactly as it was and a message explains what is wrong. A file is broken if it is not valid JSON, if `schema` is not `"teamboard-1"`, or if any card is in a column that does not exist.
6. **Undo.** An **Undo** button reverses the last action: a move, an edit, a delete, an add, or an advance day. Pressing it again goes one step further back (at least 20 steps).
7. Overdue due dates (before today's date) are shown in red. Cards can be filtered by assignee and searched by title.

## Test hooks
So that the board can be checked by a script, please give the page these attributes (the look is up to you):

- Each column: an element with `data-column="To Do"` (and so on with the column name) that contains its cards.
- Each card: an element with `data-card-id="C1"`, plus `data-assignee` and `data-priority`. It contains the title text, a checkbox `input[type=checkbox][data-role="check"]` for each checklist item (in order), a `select[data-role="move"]` whose options are the four column names (choosing one moves the card), a button `[data-role="edit-card"]` and a button `[data-role="delete-card"]`.
- A label that appears inside an overdue card: an element with `data-role="badge"` containing the text `Overdue review`.
- The latest refusal or error message: one element with `data-role="message"`.
- Buttons: `[data-role="add-card"]`, `[data-role="advance-day"]`, `[data-role="undo"]`, `[data-role="export"]`, and a file input `input[type=file][data-role="import"]`.
- The add/edit form: fields named `title`, `assignee`, `priority`, `due` and a textarea named `checklist` (one step per line), and a button `[data-role="save-card"]`.
- Search box `[data-role="search"]` and assignee filter `select[data-role="filter-assignee"]`.
