"""
Veto pre-execution guard for browser-use actions.

Validates every browser action against a Veto policy server before execution.
Supports session tracking, budget enforcement, counters, and dynamic constraints.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger('browser_use.veto')


class VetoConfig(BaseModel):
	"""Configuration for the Veto guard."""

	model_config = ConfigDict(extra='forbid')

	api_key: str = Field(description='Veto API key (veto_...)')
	endpoint: str = Field(default='https://api.veto.so', description='Veto API base URL')
	session_id: str | None = Field(default=None, description='Session ID for budget/counter tracking')
	agent_id: str = Field(default='browser-use', description='Agent identifier')
	timeout: float = Field(default=5.0, description='Request timeout in seconds')
	fail_open: bool = Field(default=False, description='Allow actions if Veto is unreachable')
	log_decisions: bool = Field(default=True, description='Log all Veto decisions')


class VetoDecision(BaseModel):
	"""Result of a Veto validation check."""

	model_config = ConfigDict(extra='allow')

	allowed: bool
	decision: str  # 'allow', 'deny', 'require_approval'
	reason: str | None = None
	approval_id: str | None = None
	latency_ms: int = 0
	session: dict[str, Any] | None = None


class VetoGuard:
	"""
	Pre-execution guard that validates browser actions against Veto policies.

	Integrates with the Veto policy engine to enforce:
	- URL allowlists/blocklists (navigate action)
	- Input content policies (type/input actions)
	- Click target restrictions
	- Session budgets and counters
	- Dynamic constraints via expressions

	Usage:
	    veto = VetoGuard(api_key="veto_...", session_id="session-123")
	    agent = Agent(task="...", llm=llm, veto=veto)

	Or with VetoConfig:
	    config = VetoConfig(api_key="veto_...", fail_open=True)
	    veto = VetoGuard(config=config)
	"""

	def __init__(
		self,
		api_key: str | None = None,
		endpoint: str = 'https://api.veto.so',
		session_id: str | None = None,
		agent_id: str = 'browser-use',
		timeout: float = 5.0,
		fail_open: bool = False,
		config: VetoConfig | None = None,
	):
		if config:
			self.config = config
		else:
			if not api_key:
				raise ValueError('api_key is required (or pass config=VetoConfig(...))')
			self.config = VetoConfig(
				api_key=api_key,
				endpoint=endpoint.rstrip('/'),
				session_id=session_id or f'browser-use-{int(time.time())}',
				agent_id=agent_id,
				timeout=timeout,
				fail_open=fail_open,
			)

		self._client = httpx.AsyncClient(
			base_url=f'{self.config.endpoint}/v1',
			headers={
				'X-Veto-API-Key': self.config.api_key,
				'Content-Type': 'application/json',
			},
			timeout=self.config.timeout,
		)
		self._action_count = 0

	async def validate_action(
		self,
		action_name: str,
		action_params: dict[str, Any],
		current_url: str | None = None,
		page_title: str | None = None,
	) -> VetoDecision:
		"""
		Validate a browser action before execution.

		Maps browser-use action names to Veto tool names and sends the
		action parameters for policy evaluation.

		Args:
		    action_name: The browser-use action (click, input, navigate, etc.)
		    action_params: The action parameters (index, text, url, etc.)
		    current_url: Current page URL for context
		    page_title: Current page title for context

		Returns:
		    VetoDecision with allowed=True/False and reason
		"""
		self._action_count += 1

		# Build the tool call arguments for Veto
		tool_name = f'browser_{action_name}'
		arguments: dict[str, Any] = {**action_params}

		# Add context fields
		if current_url:
			arguments['current_url'] = current_url
		if page_title:
			arguments['page_title'] = page_title
		arguments['action_index'] = self._action_count

		context: dict[str, Any] = {
			'agentId': self.config.agent_id,
		}
		if self.config.session_id:
			context['sessionId'] = self.config.session_id

		try:
			response = await self._client.post(
				'/validate',
				json={
					'toolName': tool_name,
					'arguments': arguments,
					'context': context,
				},
			)
			response.raise_for_status()
			data = response.json()

			decision = VetoDecision(
				allowed=data.get('decision') == 'allow',
				decision=data.get('decision', 'deny'),
				reason=data.get('reason'),
				approval_id=data.get('approval_id'),
				latency_ms=data.get('latencyMs', 0),
				session=data.get('session'),
			)

			if self.config.log_decisions:
				level = logging.INFO if decision.allowed else logging.WARNING
				logger.log(
					level,
					'Veto %s: %s %s%s [%dms]',
					decision.decision.upper(),
					tool_name,
					_summarize_params(action_params),
					f' — {decision.reason}' if decision.reason else '',
					decision.latency_ms,
				)

			return decision

		except httpx.HTTPStatusError as e:
			if e.response.status_code == 404:
				# No policy for this tool — allow by default
				if self.config.log_decisions:
					logger.debug('Veto: no policy for %s, allowing', tool_name)
				return VetoDecision(allowed=True, decision='allow', reason='No policy configured')

			logger.error('Veto API error: %s %s', e.response.status_code, e.response.text[:200])
			return self._fail_decision(f'API error: {e.response.status_code}')

		except (httpx.ConnectError, httpx.TimeoutException) as e:
			logger.error('Veto unreachable: %s', e)
			return self._fail_decision(f'Veto unreachable: {e}')

		except Exception as e:
			logger.error('Veto validation error: %s', e)
			return self._fail_decision(str(e))

	def _fail_decision(self, reason: str) -> VetoDecision:
		"""Return a decision based on fail_open configuration."""
		if self.config.fail_open:
			logger.warning('Veto: failing open — %s', reason)
			return VetoDecision(allowed=True, decision='allow', reason=f'fail_open: {reason}')
		return VetoDecision(allowed=False, decision='deny', reason=reason)

	async def close(self) -> None:
		"""Close the HTTP client."""
		await self._client.aclose()


def _summarize_params(params: dict[str, Any], max_len: int = 60) -> str:
	"""Summarize action params for logging."""
	if not params:
		return '{}'

	parts = []
	for k, v in params.items():
		if isinstance(v, str) and len(v) > 30:
			v = v[:27] + '...'
		parts.append(f'{k}={v!r}')

	summary = ', '.join(parts)
	if len(summary) > max_len:
		summary = summary[: max_len - 3] + '...'
	return f'{{{summary}}}'
