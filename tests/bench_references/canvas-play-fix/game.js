const board = document.getElementById("board");
const ctx = board.getContext("2d");
const COINS = [[360, 80], [120, 140], [300, 220], [80, 60]];
const game = { paddle: 220, score: 0, coin: 0 };
window.game = game;

function draw() {
  ctx.clearRect(0, 0, board.width, board.height);
  ctx.fillStyle = "#335";
  ctx.fillRect(game.paddle, 290, 60, 12);
  const [cx, cy] = COINS[game.coin % COINS.length];
  ctx.beginPath();
  ctx.arc(cx, cy, 14, 0, Math.PI * 2);
  ctx.fillStyle = "#e8b400";
  ctx.fill();
  ctx.fillStyle = "#000";
  ctx.font = "20px sans-serif";
  ctx.fillText("Score: " + game.score, 12, 28);
}

document.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft") game.paddle = Math.max(0, game.paddle - 20);
  if (event.key === "ArrowRight") game.paddle = Math.min(board.width - 60, game.paddle + 20);
  draw();
});

board.addEventListener("click", (event) => {
  // The click in the canvas's own coordinates: inside its border, through any CSS scaling.
  const rect = board.getBoundingClientRect();
  const x = (event.clientX - rect.left - board.clientLeft) * board.width / board.clientWidth;
  const y = (event.clientY - rect.top - board.clientTop) * board.height / board.clientHeight;
  const [cx, cy] = COINS[game.coin % COINS.length];
  if (Math.hypot(x - cx, y - cy) <= 14) {
    game.score += 1;
    game.coin += 1;
    draw();
  }
});

draw();
