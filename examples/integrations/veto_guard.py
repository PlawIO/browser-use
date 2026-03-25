"""
Example: Browser-Use with Veto Guardrails

This example shows how to run a browser agent with Veto policy enforcement.
Every browser action (navigate, click, type, etc.) is validated against your
Veto policies before execution.

Setup:
    1. Get a Veto API key from https://veto.so
    2. Create policies for browser_navigate, browser_click, browser_input, etc.
    3. Set VETO_API_KEY and OPENAI_API_KEY environment variables

Example policies you might create:
    - browser_navigate: only allow navigation to *.example.com domains
    - browser_input: block typing of passwords/SSNs into non-HTTPS forms
    - Session budget: limit total actions per session to 50
    - Counter: max 3 concurrent tabs open
"""

import asyncio
import os

from browser_use import Agent, ChatOpenAI, VetoGuard


async def main():
	# Initialize Veto guard
	veto = VetoGuard(
		api_key=os.environ['VETO_API_KEY'],
		endpoint=os.environ.get('VETO_ENDPOINT', 'https://api.veto.so'),
		session_id='demo-session',
		agent_id='browser-agent-demo',
		fail_open=False,  # Block actions if Veto is unreachable
	)

	# Create agent with Veto guard
	agent = Agent(
		task='Go to example.com and find the main heading text',
		llm=ChatOpenAI(model='gpt-4o'),
		veto=veto,
	)

	result = await agent.run()
	print(f'Result: {result}')

	# Clean up
	await veto.close()


if __name__ == '__main__':
	asyncio.run(main())
