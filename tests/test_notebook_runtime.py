from __future__ import annotations
#
###################################################################################################
###################################################################################################
################################################################################################### Standard Modules
#
import ast
import json
import sys
import types

from pathlib import Path

import pytest
#
###################################################################################################
###################################################################################################
################################################################################################### Constants
#
ROOT      = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted(ROOT.glob('s[0-9][0-9]_*/code.ipynb'))
#
###################################################################################################
###################################################################################################
################################################################################################### Notebook Validation
#
@pytest.mark.parametrize('path', NOTEBOOKS, ids=[p.parent.name for p in NOTEBOOKS])
def test_notebook_cells_compile(path:Path) -> None:

    """
        Require every tutorial notebook to contain executable Python cells.

        Jupyter permits top-level await, which the final s16/s17 cells use.
    """

    assert (len(NOTEBOOKS) == 17)

    notebook = json.loads(path.read_text(encoding='utf-8'))
    assert (notebook['nbformat'] == 4)

    for cell in notebook['cells']:

        assert (cell['cell_type'] == 'code')
        source = ''.join(cell['source'])
        compile(source, str(path), 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    #
#


@pytest.mark.parametrize('path', NOTEBOOKS[1:], ids=[p.parent.name for p in NOTEBOOKS[1:]])
def test_notebook_openai_bridge_round_trip(path:Path, monkeypatch:pytest.MonkeyPatch) -> None:

    """
        Verify the notebook adapter translates tool calls and results offline.

        This prevents missing client aliases and broken tool-call IDs from
        silently passing JSON and syntax-only notebook checks.
    """

    requests = []

    class FakeOpenAI:

        """Provide a network-free Chat Completions client for adapter tests."""

        def __init__(self, *, api_key:str, base_url:str, default_headers:dict) -> None:

            assert (default_headers['x-opencode-session'].startswith('ses_'))
            assert (default_headers['User-Agent'] == 'learn-claude-code/1.0')

            self.chat = types.SimpleNamespace(
                completions = types.SimpleNamespace(create=self.create)
            )
        #

        def create(self, **kwargs):

            """Return one tool call, followed by a final assistant response."""

            requests.append(kwargs)
            if (kwargs['messages'][-1]['role'] == 'tool'):

                calls = None
            #
            else:

                calls = [types.SimpleNamespace(
                    id       = 'call_test',
                    function = types.SimpleNamespace(
                        name      = 'read_file',
                        arguments = '{"path":"README.md"}',
                    ),
                )]
            #

            return (types.SimpleNamespace(
                choices = [types.SimpleNamespace(
                    message       = types.SimpleNamespace(content='ok', tool_calls=calls),
                    finish_reason = ('tool_calls') if (calls) else ('stop'),
                )],
                usage = types.SimpleNamespace(prompt_tokens=7, completion_tokens=3),
            ))
        #
    #

    fake_openai = types.ModuleType('openai')
    fake_openai.OpenAI = FakeOpenAI
    monkeypatch.setitem(sys.modules, 'openai', fake_openai)

    notebook = json.loads(path.read_text(encoding='utf-8'))
    bridge   = ''.join(notebook['cells'][1]['source'])
    context  = {
        '_nb_os'         : types.SimpleNamespace(getenv=lambda name:None),
        '_notebook_key'  : 'test-key',
        '_notebook_url'  : 'https://example.invalid/v1',
    }

    exec(compile(bridge, str(path), 'exec'), context)

    content = ''.join(''.join(cell['source']) for cell in notebook['cells'])
    assert ('from anthropic import Anthropic' not in content)
    assert ('Anthropic(' not in content)
    assert (('_legacy_import.Anthropic' in content) == (
        path.parent.name in {'s15_integrated_harness', 's16_workflow_runtime'}
    ))

    client = context['NotebookOpenAI']()
    assert (isinstance(client, FakeOpenAI))
    first  = client.messages.create(
        model    = 'test',
        system   = 'system',
        messages = [{'role': 'user', 'content': 'Read the file'}],
        tools    = [{'name': 'read_file', 'input_schema': {'type': 'object'}}],
    )

    assert (first.stop_reason == 'tool_use')
    tool = next(block for block in first.content if block.type == 'tool_use')
    assert (tool.id == 'call_test')
    assert (tool.input == {'path': 'README.md'})

    final = client.messages.create(
        model    = 'test',
        messages = [
            {'role': 'user', 'content': 'Read the file'},
            {'role': 'assistant', 'content': first.content},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': tool.id, 'content': 'File content'},
            ]},
        ],
    )

    assert (final.stop_reason == 'end_turn')
    assert (requests[-1]['messages'][-1] == {
        'role': 'tool', 'tool_call_id': 'call_test', 'content': 'File content',
    })
#
