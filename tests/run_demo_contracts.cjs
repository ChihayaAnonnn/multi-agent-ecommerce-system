"use strict";

const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "..");
const context = vm.createContext({
    console,
    setTimeout,
    clearTimeout,
    print: (...values) => console.log(...values),
    quit: (code = 0) => {
        process.exitCode = code;
    },
});
context.load = (filename) => {
    const resolved = path.resolve(root, filename);
    vm.runInContext(fs.readFileSync(resolved, "utf8"), context, { filename: resolved });
};
context.load("tests/commerce_demo_contracts.js");
