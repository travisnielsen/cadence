/**
 * Application Insights telemetry for the Cadence frontend.
 *
 * Initialises the browser SDK and the React plugin so every component
 * rendered inside `<AppInsightsContext.Provider>` gets automatic
 * interaction and lifecycle tracking.
 *
 * Connection configuration is split into two env vars to avoid
 * semicolon-escaping issues in GitHub Actions:
 *
 *   NEXT_PUBLIC_APPINSIGHTS_INSTRUMENTATION_KEY  – required
 *   NEXT_PUBLIC_APPINSIGHTS_INGESTION_ENDPOINT   – optional (SDK default)
 *
 * These are merged into a full connection string at runtime.
 */

import {
    AppInsightsContext,
    ReactPlugin,
} from "@microsoft/applicationinsights-react-js";
import {
    ApplicationInsights,
    type ICustomProperties,
} from "@microsoft/applicationinsights-web";

// ---------------------------------------------------------------------------
// Build connection string from split env vars
// ---------------------------------------------------------------------------

const instrumentationKey =
    process.env.NEXT_PUBLIC_APPINSIGHTS_INSTRUMENTATION_KEY ?? "";

const ingestionEndpoint =
    process.env.NEXT_PUBLIC_APPINSIGHTS_INGESTION_ENDPOINT ?? "";

function buildConnectionString(): string | undefined {
    if (!instrumentationKey) return undefined;

    let cs = `InstrumentationKey=${instrumentationKey}`;

    if (ingestionEndpoint) {
        cs += `;IngestionEndpoint=${ingestionEndpoint}`;
    }

    return cs;
}

// ---------------------------------------------------------------------------
// Singleton instances
// ---------------------------------------------------------------------------

const reactPlugin = new ReactPlugin();

const connectionString = buildConnectionString();

const appInsights = new ApplicationInsights({
    config: {
        connectionString,
        extensions: [reactPlugin],
        extensionConfig: {
            [reactPlugin.identifier]: {},
        },

        // Automatically track client-side route changes
        enableAutoRouteTracking: true,

        // Track fetch/XHR as dependency calls (SSE, API requests)
        disableFetchTracking: false,
        enableCorsCorrelation: true,

        // Full telemetry by default – reduce in high-traffic deployments
        samplingPercentage: 100,
    },
});

// Only load (and start sending telemetry) when a key is configured
if (connectionString) {
    appInsights.loadAppInsights();
    appInsights.trackPageView();
}

// ---------------------------------------------------------------------------
// Convenience helpers for custom event tracking
// ---------------------------------------------------------------------------

/** Track a named custom event with optional properties. */
function trackEvent(
    name: string,
    properties?: ICustomProperties,
): void {
    if (!connectionString) return;
    appInsights.trackEvent({ name }, properties);
}

/** Track an exception. */
function trackException(error: Error, properties?: ICustomProperties): void {
    if (!connectionString) return;
    appInsights.trackException({ exception: error }, properties);
}

// ---------------------------------------------------------------------------
// Exports
// ---------------------------------------------------------------------------

export {
    appInsights,
    AppInsightsContext,
    reactPlugin,
    trackEvent,
    trackException
};

