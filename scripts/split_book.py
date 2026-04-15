from pathlib import Path

src = Path(r'Z:\agentworkspace\ragtag_crew\docs\books\Agentic-Design-Patterns.md')
out_dir = Path(r'Z:\agentworkspace\ragtag_crew\docs\books\agentic-patterns')
out_dir.mkdir(exist_ok=True)

lines = src.read_text(encoding='utf-8').split('\n')

splits = [
    (0,   324,  'frontmatter'),
    (324, 562,  'chapter-1-prompt-chaining'),
    (562, 762,  'chapter-2-routing'),
    (762, 1046, 'chapter-3-parallelization'),
    (1046,1282, 'chapter-4-reflection'),
    (1282,1739, 'chapter-5-tool-use'),
    (1739,1980, 'chapter-6-planning'),
    (1980,2290, 'chapter-7-multi-agent'),
    (2290,2724, 'chapter-8-memory-management'),
    (2724,2908, 'chapter-9-learning-adaptation'),
    (2908,3175, 'chapter-10-mcp'),
    (3175,3401, 'chapter-11-goal-setting'),
    (3401,3521, 'chapter-12-exception-handling'),
    (3521,3632, 'chapter-13-human-in-the-loop'),
    (3632,3866, 'chapter-14-knowledge-retrieval-rag'),
    (3866,4130, 'chapter-15-inter-agent-communication'),
    (4130,4412, 'chapter-16-resource-aware'),
    (4412,4801, 'chapter-17-reasoning-techniques'),
    (4801,5137, 'chapter-18-guardrails-safety'),
    (5137,5382, 'chapter-19-evaluation-monitoring'),
    (5382,5574, 'chapter-20-prioritization'),
    (5574,5769, 'chapter-21-exploration-discovery'),
    (5769,6316, 'appendix-a-advanced-prompting'),
    (6316,6414, 'appendix-b-gui-to-real-world'),
    (6414,6564, 'appendix-c-agentic-frameworks'),
    (6564,6716, 'appendix-d-agentspace'),
    (6716,6789, 'appendix-e-cli-agents'),
    (6789,7005, 'appendix-g-coding-agents'),
    (7005,7059, 'conclusion'),
    (7059,None, 'glossary-and-index'),
]

import re
for start, end, slug in splits:
    end = end if end is not None else len(lines)
    content = '\n'.join(lines[start:end]).strip()
    if not content:
        continue
    filepath = out_dir / f'{slug}.md'
    filepath.write_text(content, encoding='utf-8')
    print(f'{filepath.name}  lines={end - start}  chars={len(content):,}')
