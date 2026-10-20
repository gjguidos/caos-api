class Initializers:
    app = {
        "logger_name": "initialize app",
        "initialize_server": "Bootstrapping server",
        "initialize_di": "Initializing dependency injection container",
        "attaching_di": ("Attaching dependency injection container to the app"),
        "initialize_observable_log_types": "Observable log types: {log_types}",
        "initialize_business_domains": "Initializing business domains",
        "integrating_prometheus_metrics": "Integrating Prometheus metrics",
        "registering_event_handlers": "Registering event handlers",
        "registering_exception_handlers": "Registering exception handlers",
        "registering_internal_routes": "Registering internal routes",
        "registering_domain_routes": "Registering domain routes",
        "registering_middleware": "Registering middleware",
        "server_initialized": "Server bootstrapped successfully",
    }

    domains = {
        "logger_name": "initialize domains",
        "initializing_di": "{domains_path}: Initializing dependency injection",
        "extended_container": "✔ Extended container with: {module_name}",
        "extended_container_error": ("❌ Failed to extend container with {module_name}: {error}"),
    }
