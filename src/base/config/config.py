from src.base.config.ai_models_config import AiModelsConfig
from src.base.config.application_config import ApplicationConfig
from src.base.config.azure_config import AzureConfig
from src.base.config.azure_search_config import AzureSearchSettings
from src.base.config.blob_storage_config import BlobStorageConfig
from src.base.config.browser_session_settings import BrowserSessionSettings
from src.base.config.cache_settings import CacheSettings
from src.base.config.chromadb_config import ChromaDBSettings
from src.base.config.cognitive_event_config import CognitiveEventConfig
from src.base.config.db_settings import DatabaseSettings
from src.base.config.event_config import EventConfig
from src.base.config.logger_config import LoggerConfig
from src.base.config.mcp_proxy_config import MCPProxyConfig
from src.base.config.messaging_config import MessagingConfig
from src.base.config.orchestrator_config import OrchestratorConfig
from src.base.config.run_report_config import RunReportConfig
from src.base.config.sacred_keys import SacredKeys
from src.base.config.security_config import SecurityConfig
from src.base.config.text.config import TextConfigNew
from src.base.config.user_management_config import UserManagementConfig


class Settings:
    """
    Aggregated settings for the application.
    """

    def __init__(self):
        self.event_config: EventConfig = EventConfig(self)
        self.ai_models: AiModelsConfig = AiModelsConfig()
        self.application: ApplicationConfig = ApplicationConfig()
        self.browser_session: BrowserSessionSettings = BrowserSessionSettings()
        self.cache: CacheSettings = CacheSettings()
        self.database: DatabaseSettings = DatabaseSettings()
        self.logger: LoggerConfig = LoggerConfig()
        self.security: SecurityConfig = SecurityConfig()
        self.texts_new = TextConfigNew()
        self.chromadb: ChromaDBSettings = ChromaDBSettings()
        self.azure_search: AzureSearchSettings = AzureSearchSettings()
        self.sacred_keys: SacredKeys = SacredKeys()
        self.azure: AzureConfig = AzureConfig()
        self.user_management: UserManagementConfig = UserManagementConfig()
        self.mcp_proxy: MCPProxyConfig = MCPProxyConfig()
        self.blob_storage: BlobStorageConfig = BlobStorageConfig()
        self.run_report: RunReportConfig = RunReportConfig()
        self.orchestrator: OrchestratorConfig = OrchestratorConfig()
        self.messaging: MessagingConfig = MessagingConfig()
        self.cognitive_event: CognitiveEventConfig = CognitiveEventConfig()


settings = Settings()
