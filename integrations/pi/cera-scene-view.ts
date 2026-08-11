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

import { writerContextPlan } from "./cera-scene-context.ts";

const MAX_READ_BYTES = 64 * 1024;
const MAX_CONTEXT_BYTES = 64 * 1024;
const MAX_WRITER_GENESIS_FILE_BYTES = 128 * 1024;
const MAX_WRITER_CONTEXT_BYTES = 384 * 1024;
const DEEPSEEK_MAXIMUM_REQUEST_BYTES = 512 * 1024;
const WRITER_NON_CONTEXT_REQUEST_RESERVE_BYTES = 128 * 1024;
const MAX_WRITER_SERIALIZED_CONTEXT_BYTES =
	DEEPSEEK_MAXIMUM_REQUEST_BYTES - WRITER_NON_CONTEXT_REQUEST_RESERVE_BYTES;
const MAX_RESULTS = 80;
const ROOT_ENV = "CERA_PI_VIEW_ROOT";
const MAX_TOOL_CALLS_ENV = "CERA_PI_MAX_TOOL_CALLS";
const PURPOSE_ENV = "CERA_PI_PURPOSE";
const WRITER_EXCLUDED_PATHS = new Set(["PRIMARY_SEQUENCE.json"]);
const WRITER_GENESIS_PATH_PREFIXES = [
	"characters/",
	"relationships/",
	"relevant_memories/",
] as const;
const WRITER_GENESIS_BUNDLE_V2_FIELDS = new Set([
	"schema_version",
	"character_id",
	"source_bundle_sha256",
	"source_record_count",
	"claims",
	"omitted_record_count",
	"omitted_source_record_sha256s",
	"source_accepted_branch_change_count",
	"accepted_branch_changes",
	"omitted_branch_change_count",
]);
const WRITER_GENESIS_CLAIM_FIELDS = new Set([
	"source_schema_version",
	"source_record_sha256",
	"record_id",
	"record_version",
	"record_type",
	"claim",
	"authority",
	"epistemic_layer",
	"truth_status",
	"certainty",
	"owner_id",
	"subject_ids",
	"knowledge_owner_ids",
	"visibility",
	"knowledge_route",
	"content_class",
	"adult_eligibility",
	"story_start_presence",
	"relationship_from_id",
	"relationship_to_id",
	"source_refs",
	"valid_from",
	"valid_to",
	"supersedes",
]);
const WRITER_BRANCH_CHANGE_FIELDS = new Set([
	"accepted_turn_id",
	"change_key",
	"kind",
	"subject_ids",
	"concise_change",
	"target_key",
	"visibility",
	"knowledge_owner_id",
	"source_kind",
]);

function isWriterExcluded(path: string): boolean {
	return (
		process.env[PURPOSE_ENV] === "writer" &&
		WRITER_EXCLUDED_PATHS.has(path.replaceAll("\\", "/"))
	);
}

function isObject(value: unknown): value is Record<string, unknown> {
	return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactFields(value: Record<string, unknown>, fields: ReadonlySet<string>): boolean {
	const keys = Object.keys(value);
	return keys.length === fields.size && keys.every((key) => fields.has(key));
}

function isSha256(value: unknown): value is string {
	return typeof value === "string" && /^[0-9a-f]{64}$/.test(value);
}

function isNonNegativeSafeInteger(value: unknown): value is number {
	return typeof value === "number" && Number.isSafeInteger(value) && value >= 0;
}

function isNonEmptyString(value: unknown): value is string {
	return typeof value === "string" && value.trim().length > 0;
}

function isStringArray(value: unknown): value is string[] {
	return Array.isArray(value) && value.every(isNonEmptyString);
}

function isNullableString(value: unknown): value is string | null {
	return value === null || isNonEmptyString(value);
}

function isWriterGenesisClaim(value: unknown): boolean {
	return (
		isObject(value) &&
		hasExactFields(value, WRITER_GENESIS_CLAIM_FIELDS) &&
		value.source_schema_version === "cera.genesis_record.v1" &&
		isSha256(value.source_record_sha256) &&
		isNonEmptyString(value.record_id) &&
		isNonNegativeSafeInteger(value.record_version) &&
		value.record_version > 0 &&
		isNonEmptyString(value.record_type) &&
		isNonEmptyString(value.claim) &&
		isNonEmptyString(value.authority) &&
		isNonEmptyString(value.epistemic_layer) &&
		isNonEmptyString(value.truth_status) &&
		isNonEmptyString(value.certainty) &&
		isNullableString(value.owner_id) &&
		isStringArray(value.subject_ids) &&
		isStringArray(value.knowledge_owner_ids) &&
		isNonEmptyString(value.visibility) &&
		isNonEmptyString(value.knowledge_route) &&
		isNonEmptyString(value.content_class) &&
		isNonEmptyString(value.adult_eligibility) &&
		isNonEmptyString(value.story_start_presence) &&
		isNullableString(value.relationship_from_id) &&
		isNullableString(value.relationship_to_id) &&
		isStringArray(value.source_refs) &&
		isNullableString(value.valid_from) &&
		isNullableString(value.valid_to) &&
		isStringArray(value.supersedes)
	);
}

function isWriterBranchChange(value: unknown): boolean {
	return (
		isObject(value) &&
		hasExactFields(value, WRITER_BRANCH_CHANGE_FIELDS) &&
		isNonEmptyString(value.accepted_turn_id) &&
		isNonEmptyString(value.change_key) &&
		typeof value.kind === "string" &&
		["character_development", "relationship", "knowledge"].includes(value.kind) &&
		isStringArray(value.subject_ids) &&
		isNonEmptyString(value.concise_change) &&
		isNonEmptyString(value.target_key) &&
		typeof value.visibility === "string" &&
		["public", "character_private", "branch_internal_unspecified"].includes(
			value.visibility,
		) &&
		isNullableString(value.knowledge_owner_id) &&
		isNonEmptyString(value.source_kind)
	);
}

function isWriterGenesisClaimBundle(value: unknown): boolean {
	if (!isObject(value) || !isNonEmptyString(value.character_id)) return false;
	if (!isSha256(value.source_bundle_sha256) || !Array.isArray(value.claims)) return false;
	if (!value.claims.every(isWriterGenesisClaim)) return false;
	if (
		value.schema_version !== "cera.pi_scene.writer_genesis_claim_bundle.v2" ||
		!hasExactFields(value, WRITER_GENESIS_BUNDLE_V2_FIELDS) ||
		!isNonNegativeSafeInteger(value.source_record_count) ||
		!isNonNegativeSafeInteger(value.omitted_record_count) ||
		!Array.isArray(value.omitted_source_record_sha256s) ||
		!value.omitted_source_record_sha256s.every(isSha256) ||
		!isNonNegativeSafeInteger(value.source_accepted_branch_change_count) ||
		!Array.isArray(value.accepted_branch_changes) ||
		!value.accepted_branch_changes.every(isWriterBranchChange) ||
		!isNonNegativeSafeInteger(value.omitted_branch_change_count)
	) {
		return false;
	}
	return (
		value.source_record_count > 0 &&
		value.source_record_count === value.claims.length + value.omitted_record_count &&
		value.omitted_record_count === value.omitted_source_record_sha256s.length &&
		value.source_accepted_branch_change_count ===
			value.accepted_branch_changes.length + value.omitted_branch_change_count
	);
}

function assertCurrentWriterProjection(controlData: Buffer): void {
	let control: unknown;
	try {
		control = JSON.parse(controlData.toString("utf8"));
	} catch {
		throw new Error("Writer context projection authority is unreadable");
	}
	if (
		!isObject(control) ||
		control.schema_version !== "cera.pi_scene.writer_authority_order.v13" ||
		!isObject(control.context_projection) ||
		control.context_projection.schema_version !==
			"cera.pi_scene.writer_context_projection.v2"
	) {
		throw new Error("Writer context projection authority is stale");
	}
}

function writerSemanticFileBound(path: string, data: Buffer): number {
	const normalized = path.replaceAll("\\", "/");
	if (!WRITER_GENESIS_PATH_PREFIXES.some((prefix) => normalized.startsWith(prefix))) {
		return MAX_READ_BYTES;
	}
	let value: unknown;
	try {
		value = JSON.parse(data.toString("utf8"));
	} catch {
		return MAX_READ_BYTES;
	}
	if (
		isObject(value) &&
		typeof value.schema_version === "string" &&
		value.schema_version.startsWith("cera.pi_scene.writer_genesis_claim_bundle.")
	) {
		if (!isWriterGenesisClaimBundle(value)) {
			throw new Error("Writer Genesis projection is stale or invalid");
		}
		return MAX_WRITER_GENESIS_FILE_BYTES;
	}
	return MAX_READ_BYTES;
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

async function writerContextPacket(
	root: string,
): Promise<{ text: string; files: number; serializedContextBytes: number }> {
	const controlPath = "zz_CURRENT_TURN_AUTHORITY.json";
	const sourcePath = "USER_PROMPT.txt";
	const availablePaths = await allFiles(root);
	const controlTarget = (await confinedPath(controlPath)).target;
	const controlData = await readFile(controlTarget);
	if (controlData.byteLength > MAX_READ_BYTES) {
		throw new Error("context file exceeds the read bound");
	}
	assertCurrentWriterProjection(controlData);
	const plan = writerContextPlan(controlData.toString("utf8"), availablePaths);
	const sections: string[] = [];
	const add = async (label: string, path: string) => {
		const target = (await confinedPath(path)).target;
		const data = await readFile(target);
		if (data.byteLength > MAX_READ_BYTES) throw new Error("context file exceeds the read bound");
		sections.push(`===== ${label} =====\n${data.toString("utf8")}`);
	};
	await add("WRITER CONTROL | zz_CURRENT_TURN_AUTHORITY.json", controlPath);
	await add(
		"USER-SUPPLIED STORY MATERIAL | USER_PROMPT.txt | PLANNER ADJUDICATES COMPLETION, ATTEMPT, INTERRUPTION, AND PENDING DIRECTION",
		sourcePath,
	);
	for (const path of plan.semanticPaths) {
		const target = (await confinedPath(path)).target;
		const data = await readFile(target);
		if (data.byteLength > writerSemanticFileBound(path, data)) {
			throw new Error("context file exceeds the read bound");
		}
		sections.push(`===== SUPPORTING ACCEPTED CONTEXT | ${path} =====\n${data.toString("utf8")}`);
	}
	await add(
		`LOGIC-OWNER REALIZATION AUTHORITY | ${plan.authorityPath} | PRESENT WITH NARRATIVE FREEDOM`,
		plan.authorityPath,
	);
	const text = sections.join("\n\n");
	if (Buffer.byteLength(text, "utf8") > MAX_WRITER_CONTEXT_BYTES) {
		throw new Error("Writer view exceeds the context bound");
	}
	const serializedContextBytes = Buffer.byteLength(JSON.stringify(text), "utf8");
	if (serializedContextBytes > MAX_WRITER_SERIALIZED_CONTEXT_BYTES) {
		throw new Error("Writer view exceeds the serialized request bound");
	}
	return { text, files: plan.semanticPaths.length + 3, serializedContextBytes };
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
						serializedContextBytes: packet.serializedContextBytes,
						serializedContextLimitBytes: MAX_WRITER_SERIALIZED_CONTEXT_BYTES,
						nonContextRequestReserveBytes: WRITER_NON_CONTEXT_REQUEST_RESERVE_BYTES,
						maximumRequestBytes: DEEPSEEK_MAXIMUM_REQUEST_BYTES,
						files: packet.files,
						packet: "cera.writer_context_packet.v8",
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
