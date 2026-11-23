Please make a two-player game as one page, `board.html`, that works when the file is opened directly. Two people take turns on the same screen to get three of their marks in a row.

- The board is 3 by 3. Each cell is an element with `data-cell` set to 0 to 8, row by row from the top left.
- Player X moves first, then the players take turns. Clicking an empty cell puts the current player's mark in it, and the cell's text becomes `X` or `O`. Clicking a cell that already has a mark does nothing.
- Three of the same mark in a row, a column or a diagonal wins. When all nine cells are filled with no winner, it is a draw.
- An element with id `status` shows `X to move` or `O to move` while the game is on, then `X wins`, `O wins` or `Draw`. After a win or a draw, clicking the board does nothing.
- Elements with ids `score-x`, `score-o` and `score-draw` show how many games X has won, O has won and were drawn, as numbers.
- A button with id `new-game` clears the board for a new game, with X to move. The scores stay.
- The scores are still there after the page is reloaded. The board does not need to be.
- No scripts, styles or fonts from the internet, and no errors in the browser console.

Open the page in a browser and play a few games before you finish, to check that it works as described. Then tell me what you checked.
