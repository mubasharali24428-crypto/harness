/** Service-name registry for the generated section clients. */
/** Service keys, matching scripts/sdk-gen/specs/*.openapi.json. */
export declare const SERVICES: readonly ["github", "identity", "meeting", "orchestrator", "review", "router", "sandbox"];
export type ServiceKey = (typeof SERVICES)[number];
/** Default port per section service (deploy/docker-compose.yml). */
export declare const SERVICE_PORTS: {
    [K in ServiceKey]: number;
};
