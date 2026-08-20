"use client";

import { ExternalLink } from "lucide-react";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";

import {
    getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get,
    getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet,
    saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put,
} from "@/client/sdk.gen";
import type {
    ModelConfigurationPricingResponse,
    OrganizationAiModelConfigurationResponse,
    OrganizationAiModelConfigurationV2,
} from "@/client/types.gen";
import { type ModelConfigurationDefaultsV2 } from "@/components/AIModelConfigurationV2Editor";
import { Skeleton } from "@/components/ui/skeleton";

// Dynamic import — removes ~67KB (AIModelConfigurationV2Editor + ServiceConfigurationForm)
// from the initial bundle. Loads only when the model config page is visited.
const AIModelConfigurationV2Editor = dynamic(
    () => import("@/components/AIModelConfigurationV2Editor").then((m) => ({ default: m.AIModelConfigurationV2Editor })),
    {
        ssr: false,
        loading: () => <div className="space-y-4"><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-full" /><Skeleton className="h-10 w-3/4" /></div>,
    }
);

import { useUserConfig } from "@/context/UserConfigContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { fetchModelConfigurationPricing } from "@/lib/modelConfigurationPricing";


export default function ModelConfigurationV2({ docsUrl }: { docsUrl?: string }) {
    const auth = useAuth();
    const { refreshConfig } = useUserConfig();
    const hasFetched = useRef(false);

    const [defaults, setDefaults] = useState<ModelConfigurationDefaultsV2 | null>(null);
    const [response, setResponse] = useState<OrganizationAiModelConfigurationResponse | null>(null);
    const [pricing, setPricing] = useState<ModelConfigurationPricingResponse | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [notice, setNotice] = useState<string | null>(null);

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;

        const load = async () => {
            setLoading(true);
            setError(null);
            const [defaultsResult, configResult, pricingResult] = await Promise.all([
                getModelConfigurationV2DefaultsApiV1OrganizationsModelConfigurationsV2DefaultsGet(),
                getModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Get(),
                fetchModelConfigurationPricing(),
            ]);

            if (defaultsResult.error) {
                setError(detailFromError(defaultsResult.error, "Failed to load model configuration defaults"));
                setLoading(false);
                return;
            }
            if (configResult.error) {
                setError(detailFromError(configResult.error, "Failed to load model configuration"));
                setLoading(false);
                return;
            }

            const nextDefaults = defaultsResult.data as ModelConfigurationDefaultsV2;
            if (!nextDefaults || !configResult.data) {
                setError("Failed to load model configuration");
                setLoading(false);
                return;
            }
            setDefaults(nextDefaults);
            setResponse(configResult.data);
            setPricing(pricingResult);
            setLoading(false);
        };

        load();

    }, [auth.loading, auth.user]);

    const saveConfiguration = async (configuration: OrganizationAiModelConfigurationV2) => {
        if (!defaults) return;
        setError(null);
        setNotice(null);

        const result = await saveModelConfigurationV2ApiV1OrganizationsModelConfigurationsV2Put({
            body: configuration,
        });

        if (result.error) {
            throw new Error(detailFromError(result.error, "Failed to save model configuration"));
        }
        if (!result.data) {
            throw new Error("Failed to save model configuration");
        }

        setResponse(result.data);
        void fetchModelConfigurationPricing().then(setPricing);
        await refreshConfig();
        setNotice("Model configuration saved");
    };

    if (loading) {
        return (
            <div className="w-full max-w-4xl mx-auto space-y-6">
                <Skeleton className="h-10 w-80" />
                <Skeleton className="h-28 w-full" />
                <Skeleton className="h-96 w-full" />
            </div>
        );
    }

    return (
        <div className="w-full max-w-4xl mx-auto space-y-6">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div>
                    <h1 className="text-3xl font-bold">AI Models Configuration</h1>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Organization-scoped model settings.{" "}
                        {docsUrl && (
                            <a href={docsUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-0.5 underline">
                                Learn more <ExternalLink className="h-3 w-3" />
                            </a>
                        )}
                    </p>
                </div>
            </div>

            {error && (
                <div className="rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
                    {error}
                </div>
            )}
            {notice && (
                <div className="rounded-md border border-green-500/40 bg-green-500/10 px-4 py-3 text-sm text-green-700 dark:text-green-300">
                    {notice}
                </div>
            )}

            {defaults && response && (
                <AIModelConfigurationV2Editor
                    defaults={defaults}
                    configuration={response.configuration}
                    effectiveConfiguration={response.effective_configuration}
                    pricing={pricing}
                    onSave={saveConfiguration}
                />
            )}
        </div>
    );
}
