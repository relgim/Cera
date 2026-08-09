/** Route-aware authority selection for the confined CERA Pi Writer view. */

export type WriterContextPlan = {
	authorityPath: "RESPONSE_SEQUENCE.json" | "ADULT_HANDOFF.json";
	semanticPaths: string[];
};

type CurrentTurnAuthority = {
	current_route?: unknown;
	current_purpose?: unknown;
	current_primary_authority_path?: unknown;
};

export function writerContextPlan(
	controlText: string,
	availablePaths: string[],
): WriterContextPlan {
	let value: CurrentTurnAuthority;
	try {
		value = JSON.parse(controlText) as CurrentTurnAuthority;
	} catch {
		throw new Error("current Writer authority is not valid JSON");
	}
	if (value.current_purpose !== "writer") {
		throw new Error("current Writer authority is not bound to writer purpose");
	}
	const expected =
		value.current_route === "ordinary"
			? "RESPONSE_SEQUENCE.json"
			: value.current_route === "adult"
				? "ADULT_HANDOFF.json"
				: null;
	if (expected === null || value.current_primary_authority_path !== expected) {
		throw new Error("current Writer authority route/path binding is invalid");
	}
	if (!availablePaths.includes(expected)) {
		throw new Error("current Writer authority file is unavailable");
	}
	const excluded = new Set([
		"MANIFEST.json",
		"zz_CURRENT_TURN_AUTHORITY.json",
		"USER_PROMPT.txt",
		expected,
	]);
	return {
		authorityPath: expected,
		semanticPaths: availablePaths.filter((path) => !excluded.has(path)),
	};
}
