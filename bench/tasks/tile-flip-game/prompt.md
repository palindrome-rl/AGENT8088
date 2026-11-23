Please make a small puzzle game as one page, `game.html`, that works when the file is opened directly.

- The board is a 5 by 5 grid of tiles. Each tile is an element with the attributes `data-row` and `data-col` (0 to 4, counted from the top left) and `data-lit`, which is `1` while the tile is lit and `0` while it is not.
- Clicking a tile flips it and the tiles directly above, below, left and right of it. Diagonal tiles do not change. A tile at the edge has fewer neighbours.
- The start pattern comes from the page address: `game.html?start=` followed by 25 characters, each `0` or `1`, row by row from the top left. Without a valid `start`, the game starts with only these five tiles lit: row 1 column 2, row 2 columns 1, 2 and 3, and row 3 column 2.
- An element with id `moves` shows how many tiles have been clicked so far, as a number.
- When no tile is lit, an element with id `status` shows the word `Solved`, and clicking a tile changes nothing. Before that it must not say `Solved`.
- A button with id `restart` puts the start pattern back and sets the move count to 0.
- No scripts, styles or fonts from the internet, and no errors in the browser console.

Open the page in a browser and play it before you finish, to check that it works as described. Then tell me what you checked.
