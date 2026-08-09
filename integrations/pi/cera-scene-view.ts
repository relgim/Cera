/**
 * Dedicated CERA Pi Scene tools.
 *
 * Pi is launched with --no-builtin-tools and this extension explicitly.  The
 * tools below can only observe the Python-materialized candidate view.
 * They expose no shell, process, network, repository, database, or write API.
 */

import type { TextContent } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { lstat, readFile, readdir, realpath } from "node:fs/promises";
import { isAbsolute, relative, resolve, sep } from "node:path";
import { Type } from "typebox";

const MAX_READ_BYTES = 64 * 1024;
const MAX_CONTEXT_BYTES = 64 * 1024;
const MAX_RESULTS = 80;
const ROOT_ENV = "CERA_PI_VIEW_ROOT";
const MAX_TOOL_CALLS_ENV = "CERA_PI_MAX_TOOL_CALLS";
const PURPOSE_ENV = "CERA_PI_PURPOSE";
const WRITER_EXCLUDED_PATHS = new Set(["PRIMARY_SEQUENCE.json"]);

function isWriterExcluded(path: string): boolean {
	return (
		process.env[PURPOSE_ENV] === "writer" &&
		WRITER_EXCLUDED_PATHS.has(path.replaceAll("\\", "/"))
	);
}

function configuredRoot(): string {
	const value = process.env[ROOT_ENV];
	if (!value || !value.trim()) throw new Error(`${ROOT_ENV} is required`);
	return resolve(value);
}

function normalizeRelative(value: string): string {
	const cleaned = value.startsWith("@") ? value.slice(1) : value;
	if (
		!cleaned ||
		isAbsolute(cleaned) ||
		/^[A-Za-z]:/.test(cleaned) ||
		cleaned.startsWith("\\\\") ||
		cleaned.includes("\0")
	) {
		throw new Error("path must be a non-empty relative Writer-view path");
	}
	const parts = cleaned.replaceAll("\\", "/").split("/");
	if (parts.some((part) => !part || part === "." || part === "..")) {
		throw new Error("path traversal is forbidden");
	}
	return parts.join(sep);
}

async function confinedPath(value: string): Promise<{ root: string; target: string }> {
	const root = await realpath(configuredRoot());
	const normalized = normalizeRelative(value);
	if (isWriterExcluded(normalized)) {
		throw new Error("path is retained for Python custody and excluded from Writer access");
	}
	let lexical = root;
	for (const part of normalized.split(sep)) {
		lexical = resolve(lexical, part);
		const stats = await lstat(lexical);
		if (stats.isSymbolicLink()) throw new Error("symbolic links are forbidden");
	}
	const target = await realpath(lexical);
	const rel = relative(root, target);
	if (!rel || rel === ".") return { root, target };
	if (rel.startsWith(`..${sep}`) || rel === ".." || isAbsolute(rel)) {
		throw new Error("path escaped the Writer view");
	}
	return { root, target };
}

async function allFiles(root: string): Promise<string[]> {
	const rootReal = await realpath(root);
	const pending = [rootReal];
	const output: string[] = [];
	while (pending.length) {
		const directory = pending.pop()!;
		for (const entry of await readdir(directory, { withFileTypes: true })) {
			if (entry.isSymbolicLink()) continue;
			const target = resolve(directory, entry.name);
			const rel = relative(rootReal, target);
			if (rel.startsWith(`..${sep}`) || isAbsolute(rel)) continue;
			if (entry.isDirectory()) pending.push(target);
			else if (entry.isFile()) {
				const normalized = rel.replaceAll(sep, "/");
				if (!isWriterExcluded(normalized)) output.push(normalized);
			}
		}
	}
	return output.sort();
}

async function writerContextPacket(root: string): Promise<{ text: string; files: number }> {
	const responsePath = "RESPONSE_SEQUENCE.json";
	const controlPath = "zz_CURRENT_TURN_AUTHORITY.json";
	const sourcePath = "USER_PROMPT.txt";
	const startGatePath = "zz_RESPONSE_START_GATE.json";
	const semanticPaths = (await allFiles(root)).filter(
		(path) =>
			!["MANIFEST.json", responsePath, controlPath, sourcePath, startGatePath].includes(
				path,
			),
	);
	const sections: string[] = [];
	const add = async (label: string, path: string) => {
		const target = (await confinedPath(path)).target;
		const data = await readFile(target);
		if (data.byteLength > MAX_READ_BYTES) throw new Error("context file exceeds the read bound");
		sections.push(`===== ${label} =====\n${data.toString("utf8")}`);
	};
	await add("RESPONSE REALIZATION AUTHORITY | RESPONSE_SEQUENCE.json", responsePath);
	await add("WRITER CONTROL | zz_CURRENT_TURN_AUTHORITY.json", controlPath);
	await add(
		"COMPLETED OFF-PAGE SOURCE | USER_PROMPT.txt | CONTEXT ONLY | DO NOT NARRATE, QUOTE, PARAPHRASE, OR STAGE",
		sourcePath,
	);
	for (const path of semanticPaths) {
		await add(`SUPPORTING ACCEPTED CONTEXT | ${path}`, path);
	}
	await add(
		"FINAL RESPONSE START GATE | DERIVED NONCANONICAL EXECUTION FOCUS | zz_RESPONSE_START_GATE.json | BEGIN WITH THE SELECTED SURFACE RESPONSE NOW",
		startGatePath,
	);
	const text = sections.join("\n\n");
	if (Buffer.byteLength(text, "utf8") > MAX_CONTEXT_BYTES) {
		throw new Error("Writer view exceeds the context bound");
	}
	return { text, files: semanticPaths.length + 4 };
}

const toolGuidelines = [
	"Use only the CERA Writer-view tools. They are confined to the current branch/candidate view.",
	"Prefer one context call; Python already minimized the complete authoritative scene view.",
	"Never claim access to repositories, drives, credentials, databases, Git, shell, or files outside this view.",
];

export default function (pi: ExtensionAPI) {
	const configuredLimit = Number.parseInt(process.env[MAX_TOOL_CALLS_ENV] ?? "5", 10);
	if (!Number.isSafeInteger(configuredLimit) || configuredLimit < 1 || configuredLimit > 20) {
		throw new Error(`${MAX_TOOL_CALLS_ENV} must be between 1 and 20`);
	}
	let observedToolCalls = 0;
	pi.on("tool_call", async () => {
		observedToolCalls += 1;
		if (observedToolCalls > configuredLimit) {
			return {
				block: true,
				reason: "CERA Pi Scene tool-call bound reached",
				terminate: true,
			};
		}
	});

	pi.registerTool({
		name: "context",
		label: "Load CERA scene context",
		description: "Load the complete bounded Python-materialized CERA scene view in one call.",
		promptSnippet: "Load the complete approved CERA scene context once",
		promptGuidelines: toolGuidelines,
		parameters: Type.Object({}),
		async execute() {
			const root = await realpath(configuredRoot());
			if (process.env[PURPOSE_ENV] === "writer") {
				const packet = await writerContextPacket(root);
				return {
					content: [{ type: "text", text: packet.text }] as TextContent[],
					details: {
						bytes: Buffer.byteLength(packet.text, "utf8"),
						files: packet.files,
						packet: "cera.writer_context_packet.v5",
					},
				};
			}
			const sections: string[] = [];
			let totalBytes = 0;
			for (const rel of await allFiles(root)) {
				const target = (await confinedPath(rel)).target;
				const data = await readFile(target);
				if (data.byteLength > MAX_READ_BYTES) throw new Error("context file exceeds the read bound");
				totalBytes += data.byteLength;
				if (totalBytes > MAX_CONTEXT_BYTES) throw new Error("Writer view exceeds the context bound");
				sections.push(`===== ${rel} =====\n${data.toString("utf8")}`);
			}
			return {
				content: [{ type: "text", text: sections.join("\n\n") }] as TextContent[],
				details: { bytes: totalBytes, files: sections.length },
			};
		},
	});

	pi.registerTool({
		name: "read",
		label: "Read CERA Writer view",
		description: "Read one UTF-8 file inside the current CERA Writer view.",
		promptSnippet: "Read one approved CERA Writer-view file",
		promptGuidelines: toolGuidelines,
		parameters: Type.Object({
			path: Type.String({ description: "Relative path from the Writer-view root" }),
		}),
		async execute(_id, params) {
			const { target } = await confinedPath(params.path);
			const stats = await lstat(target);
			if (!stats.isFile()) throw new Error("read target is not a file");
			const data = await readFile(target);
			if (data.byteLength > MAX_READ_BYTES) throw new Error("file exceeds the read bound");
			return {
				content: [{ type: "text", text: data.toString("utf8") }] as TextContent[],
				details: { bytes: data.byteLength },
			};
		},
	});

	pi.registerTool({
		name: "list",
		label: "List CERA Writer view",
		description: "List one directory inside the current CERA Writer view.",
		promptSnippet: "List an approved CERA Writer-view directory",
		promptGuidelines: toolGuidelines,
		parameters: Type.Object({
			path: Type.Optional(
				Type.String({ description: "Relative directory path; omit for the view root" }),
			),
		}),
		async execute(_id, params) {
			const root = await realpath(configuredRoot());
			const target = params.path ? (await confinedPath(params.path)).target : root;
			const stats = await lstat(target);
			if (!stats.isDirectory()) throw new Error("list target is not a directory");
			const entries = (await readdir(target, { withFileTypes: true }))
				.filter((entry) => !entry.isSymbolicLink())
				.filter((entry) => {
					const child = params.path ? `${params.path}/${entry.name}` : entry.name;
					return !isWriterExcluded(child);
				})
				.map((entry) => `${entry.isDirectory() ? "directory" : "file"}\t${entry.name}`)
				.sort()
				.slice(0, MAX_RESULTS);
			return {
				content: [{ type: "text", text: entries.join("\n") || "[empty]" }] as TextContent[],
				details: { count: entries.length },
			};
		},
	});

	pi.registerTool({
		name: "find",
		label: "Find CERA Writer-view files",
		description: "Find approved files by a case-insensitive filename substring.",
		promptSnippet: "Find files within the approved CERA Writer view",
		promptGuidelines: toolGuidelines,
		parameters: Type.Object({
			name: Type.String({ description: "Filename substring" }),
		}),
		async execute(_id, params) {
			const needle = params.name.trim().toLocaleLowerCase();
			if (!needle) throw new Error("find substring is empty");
			const files = (await allFiles(configuredRoot()))
				.filter((path) => path.toLocaleLowerCase().includes(needle))
				.slice(0, MAX_RESULTS);
			return {
				content: [{ type: "text", text: files.join("\n") || "[no matches]" }] as TextContent[],
				details: { count: files.length },
			};
		},
	});

	pi.registerTool({
		name: "search",
		label: "Search CERA Writer view",
		description: "Literal, case-insensitive text search across approved UTF-8 view files.",
		promptSnippet: "Search text only within the approved CERA Writer view",
		promptGuidelines: toolGuidelines,
		parameters: Type.Object({
			query: Type.String({ description: "Literal text to search for" }),
		}),
		async execute(_id, params) {
			const query = params.query.trim().toLocaleLowerCase();
			if (!query) throw new Error("search query is empty");
			const root = await realpath(configuredRoot());
			const matches: string[] = [];
			for (const rel of await allFiles(root)) {
				const target = (await confinedPath(rel)).target;
				const data = await readFile(target);
				if (data.byteLength > MAX_READ_BYTES) continue;
				const lines = data.toString("utf8").split(/\r?\n/);
				for (let index = 0; index < lines.length; index++) {
					if (lines[index].toLocaleLowerCase().includes(query)) {
						matches.push(`${rel}:${index + 1}:${lines[index].slice(0, 300)}`);
						if (matches.length >= MAX_RESULTS) break;
					}
				}
				if (matches.length >= MAX_RESULTS) break;
			}
			return {
				content: [{ type: "text", text: matches.join("\n") || "[no matches]" }] as TextContent[],
				details: { count: matches.length },
			};
		},
	});
}
