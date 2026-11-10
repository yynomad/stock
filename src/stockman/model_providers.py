"""Explicit model API endpoints shared by the service, CLI and evaluation."""
from dataclasses import dataclass, field
import os
import re
from urllib.parse import urlsplit

GEMINI = 'gemini'
DEEPSEEK = 'deepseek'
DEFAULT_URLS = {GEMINI: 'https://generativelanguage.googleapis.com', DEEPSEEK: 'https://api.deepseek.com'}
DEFAULT_MODELS = {GEMINI: 'gemini-3.5-flash-lite', DEEPSEEK: 'deepseek-flash'}
KEY_NAMES = {GEMINI: 'GEMINI_API_KEY', DEEPSEEK: 'DEEPSEEK_API_KEY'}
ROLES = ('supervisor', 'portfolio', 'research', 'monitor')


class ModelConfigurationError(ValueError):
    """A model configuration error that is safe to show to an operator."""


def model_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:-]{0,149}', value)
            or any(part in ('', '.', '..') for part in value.split('/'))):
        raise ModelConfigurationError('invalid model name')
    return value


def endpoint_url(value):
    if not re.fullmatch(r'[!-~]{1,2048}', value) or any(char in value for char in '\\?#'):
        raise ModelConfigurationError('invalid model API URL')
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise ModelConfigurationError('invalid model API URL') from None
    if (url.scheme != 'https' or not url.hostname or url.username is not None
            or '%' in url.netloc or port == 0):
        raise ModelConfigurationError('model API URL requires HTTPS and a host without credentials')
    return value.rstrip('/')


def provider_of(model):
    declared = getattr(model, 'stockman_provider', None)
    if declared in DEFAULT_URLS:
        return declared
    return {'chat-google-generative-ai': GEMINI, 'chat-deepseek': DEEPSEEK}.get(
        getattr(model, '_llm_type', None))


@dataclass(frozen=True)
class ModelEndpoint:
    provider: str
    name: str
    key: str = field(repr=False)
    base_url: str = field(repr=False)
    grounding_key: str = field(default='', repr=False)

    def __post_init__(self):
        if self.provider not in DEFAULT_URLS:
            raise ModelConfigurationError('unsupported model provider')
        for key in (self.key, self.grounding_key):
            if key and not re.fullmatch(r'[!-~]{1,512}', key):
                raise ModelConfigurationError('invalid model API key')
        object.__setattr__(self, 'base_url', endpoint_url(self.base_url))
        if self.grounding_key and self.grounding_key == self.key and not (self.provider == GEMINI and self.official):
            raise ModelConfigurationError('Gemini grounding requires a separate API key')
        self.validate_name(self.name)

    def validate_name(self, name):
        name = model_name(name)
        if any(key and (name == key or len(key) >= 8 and (key in name or key in self.base_url))
               for key in (self.key,self.grounding_key)):
            raise ModelConfigurationError('model credentials must be separate from URL and model name')
        return name

    @property
    def official(self):
        url = urlsplit(self.base_url)
        return url.hostname == urlsplit(DEFAULT_URLS[self.provider]).hostname and url.port in (None,443)

    @property
    def search_key(self):
        if self.grounding_key:
            return self.grounding_key
        return self.key if self.provider == GEMINI and self.official else ''

    @property
    def search_model(self):
        return self.name if self.provider == GEMINI and self.official else DEFAULT_MODELS[GEMINI]

    @classmethod
    def from_environment(cls, *, env=None, provider=None, name=None):
        env = os.environ if env is None else env
        provider = provider or env.get('STOCKMAN_MODEL_PROVIDER', GEMINI)
        if provider not in DEFAULT_URLS:
            raise ModelConfigurationError('unsupported model provider')
        fallback_name = env.get('GEMINI_MODEL', DEFAULT_MODELS[GEMINI]) if provider == GEMINI else DEFAULT_MODELS[DEEPSEEK]
        return cls(provider, name or env.get('STOCKMAN_MODEL_NAME') or fallback_name,
                   env.get(KEY_NAMES[provider], ''), env.get('STOCKMAN_MODEL_BASE_URL') or DEFAULT_URLS[provider],
                   env.get('STOCKMAN_GROUNDING_API_KEY', ''))

    def role_name(self, role):
        if self.provider == GEMINI:
            return os.getenv(f'GEMINI_{role.upper()}_MODEL', self.name)
        return self.name

    def create(self, *, name=None, timeout=45, max_output=2048, json_output=False, retry=False):
        if not self.key:
            raise ModelConfigurationError('missing ' + KEY_NAMES[self.provider])
        name = self.validate_name(name or self.name)
        if self.provider == GEMINI:
            from langchain_google_genai import ChatGoogleGenerativeAI

            options = {'response_mime_type': 'application/json'} if json_output else {}
            return ChatGoogleGenerativeAI(
                model=name, api_key=self.key, vertexai=False, base_url=self.base_url,
                api_version='v1beta', client_args={'follow_redirects': False}, temperature=0,
                timeout=timeout, max_retries=2 if retry else 1, max_output_tokens=max_output, **options)

        from langchain_deepseek import ChatDeepSeek
        from openai import DefaultAsyncHttpxClient, DefaultHttpxClient

        options = {}
        if self.official:
            # The official API enables thinking by default, which changes tool continuation.
            options['extra_body'] = {'thinking': {'type': 'disabled'}}
        if json_output:
            options['model_kwargs'] = {'response_format': {'type': 'json_object'}}
        return ChatDeepSeek(
            model=name, api_key=self.key, base_url=self.base_url, temperature=0, timeout=timeout,
            max_tokens=max_output, max_retries=1 if retry else 0,
            http_client=DefaultHttpxClient(follow_redirects=False),
            http_async_client=DefaultAsyncHttpxClient(follow_redirects=False), **options)
