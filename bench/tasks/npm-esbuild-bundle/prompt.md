This folder has a small Node program in src/. src/index.js is the entry point, and it imports the `dayjs` package.

Please make it a working npm project:

1. Create package.json, install `dayjs` as a dependency and `esbuild` as a dev dependency from the npm registry.
2. Add an npm script called `build` that uses esbuild to bundle src/index.js into one file, dist/app.js, for Node, with dayjs included in the bundle so dist/app.js runs on its own.
3. Run `npm run build`, then run `node dist/app.js` and check what it prints.

When you are done, tell me what it printed and what you checked yourself.
