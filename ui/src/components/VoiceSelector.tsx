"use client";

import { Check, ChevronDown, Loader2, Pencil, Play, Search, Square, Volume2 } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { client } from "@/client/client.gen";
import { getVoicesApiV1UserConfigurationsVoicesProviderGet } from "@/client/sdk.gen";
import { VoiceInfo } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
    Dialog,
    DialogContent,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ACCENT_DISPLAY_NAMES } from "@/constants/accents";
import { LANGUAGE_DISPLAY_NAMES } from "@/constants/languages";
import { cn } from "@/lib/utils";

// Providers that have MPS voice endpoints
type TTSProviderWithVoices = "elevenlabs" | "deepgram" | "sarvam" | "cartesia" | "dograh" | "rime" | "murf" | "smallest";
const MPS_VOICE_PROVIDERS: TTSProviderWithVoices[] = ["elevenlabs", "deepgram", "sarvam", "cartesia", "dograh", "rime", "murf", "smallest"];
const ALL_FILTER_VALUE = "__all__";

interface VoiceSelectorProps {
    provider: string;
    value: string;
    onChange: (voiceId: string) => void;
    model?: string;
    language?: string;
    showFilters?: boolean;
    allowManualInput?: boolean;
    className?: string;
    /** Optional API key (e.g. typed in form but not yet saved). Used for Murf. */
    apiKey?: string;
}

export const VoiceSelector: React.FC<VoiceSelectorProps> = ({
    provider,
    value,
    onChange,
    model,
    language,
    showFilters = false,
    allowManualInput = true,
    className,
    apiKey,
}) => {
    const [isOpen, setIsOpen] = useState(false);
    const [searchTerm, setSearchTerm] = useState("");
    const [genderFilter, setGenderFilter] = useState(ALL_FILTER_VALUE);
    // Initialize languageFilter from the language prop so the picker shows filtered voices immediately
    const [languageFilter, setLanguageFilter] = useState(language || ALL_FILTER_VALUE);
    const [accentFilter, setAccentFilter] = useState(ALL_FILTER_VALUE);
    const [isManualInput, setIsManualInput] = useState(false);
    const [manualVoiceId, setManualVoiceId] = useState(value || "");
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [playingPreview, setPlayingPreview] = useState<string | null>(null);
    const [currentAudio, setCurrentAudio] = useState<HTMLAudioElement | null>(null);

    // When a language prop is provided, the server already pre-filters voices by that language.
    // So set client-side languageFilter to ALL to show everything the server returned.
    // (Some Telugu voices have their primary display language as "en" due to code-switching,
    // so filtering client-side by "te" would incorrectly hide them.)
    useEffect(() => {
        setLanguageFilter(ALL_FILTER_VALUE);
    }, [language]);

    // Check if provider has MPS voice endpoint
    const hasMPSVoiceEndpoint = useCallback((providerName: string): boolean => {
        return MPS_VOICE_PROVIDERS.includes(providerName.toLowerCase() as TTSProviderWithVoices);
    }, []);

    // Map provider names to API-compatible provider names
    const getProviderKey = useCallback((providerName: string): TTSProviderWithVoices | null => {
        const providerMap: Record<string, TTSProviderWithVoices> = {
            elevenlabs: "elevenlabs",
            deepgram: "deepgram",
            sarvam: "sarvam",
            cartesia: "cartesia",
            dograh: "dograh",
            rime: "rime",
            murf: "murf",
            smallest: "smallest",
        };
        return providerMap[providerName.toLowerCase()] || null;
    }, []);

    const fetchVoices = useCallback(async () => {
        const providerKey = getProviderKey(provider);
        if (!providerKey) {
            setVoices([]);
            return;
        }

        setIsLoading(true);
        setError(null);

        try {
            const query: { model?: string; language?: string; api_key?: string } = {};
            if (model) query.model = model;
            // Do not pre-filter the catalog by the form language. The picker
            // should show every voice; users can filter inside the list.
            if (apiKey && !apiKey.includes("***")) query.api_key = apiKey;
            const response = await getVoicesApiV1UserConfigurationsVoicesProviderGet({
                path: { provider: providerKey },
                query: Object.keys(query).length > 0 ? query : undefined,
            });

            if (response.data?.voices) {
                setVoices(response.data.voices);
            }
        } catch (err) {
            console.error("Failed to fetch voices:", err);
            setError("Failed to load voices");
            setVoices([]);
        } finally {
            setIsLoading(false);
        }
    }, [provider, model, apiKey, getProviderKey]);

    useEffect(() => {
        if (provider) {
            fetchVoices();
        }
    }, [provider, fetchVoices]);

    // Check if the current value exists in the voices list
    useEffect(() => {
        if (value && voices.length > 0) {
            const voiceExists = voices.some((v) => v.voice_id === value);
            if (!voiceExists && allowManualInput) {
                // If the value doesn't exist in the list, switch to manual input mode
                setIsManualInput(true);
                setManualVoiceId(value);
            } else if (voiceExists) {
                setIsManualInput(false);
            }
        }
    }, [value, voices, allowManualInput]);

    // Cleanup audio on unmount or when popover closes
    useEffect(() => {
        if (!isOpen && currentAudio) {
            currentAudio.pause();
            currentAudio.currentTime = 0;
            setCurrentAudio(null);
            setPlayingPreview(null);
        }
    }, [isOpen, currentAudio]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (currentAudio) {
                currentAudio.pause();
            }
        };
    }, [currentAudio]);

    const filteredVoices = voices.filter((voice) => {
        const searchLower = searchTerm.toLowerCase();
        const matchesSearch = (
            voice.name.toLowerCase().includes(searchLower) ||
            voice.voice_id.toLowerCase().includes(searchLower) ||
            (voice.description?.toLowerCase() || "").includes(searchLower) ||
            (voice.accent?.toLowerCase() || "").includes(searchLower) ||
            (voice.gender?.toLowerCase() || "").includes(searchLower) ||
            (voice.language?.toLowerCase() || "").includes(searchLower)
        );
        if (!matchesSearch) return false;
        if (genderFilter !== ALL_FILTER_VALUE && (voice.gender || "").toLowerCase() !== genderFilter) return false;
        if (languageFilter !== ALL_FILTER_VALUE && (voice.language || "").toLowerCase() !== languageFilter) return false;
        if (accentFilter !== ALL_FILTER_VALUE && (voice.accent || "").toLowerCase() !== accentFilter) return false;
        return true;
    });

    const genderOptions = Array.from(
        new Set(voices.map((voice) => voice.gender?.toLowerCase()).filter(Boolean) as string[]),
    ).sort();
    const languageOptions = Array.from(
        new Set(voices.map((voice) => voice.language?.toLowerCase()).filter(Boolean) as string[]),
    ).sort();
    const accentOptions = Array.from(
        new Set(voices.map((voice) => voice.accent?.toLowerCase()).filter(Boolean) as string[]),
    ).sort();

    const handleSelectVoice = (voiceId: string) => {
        onChange(voiceId);
        setIsOpen(false);
        setSearchTerm("");
    };

    const handleManualInputToggle = (checked: boolean) => {
        if (!allowManualInput) return;
        setIsManualInput(checked);
        if (checked) {
            setManualVoiceId(value || "");
        } else {
            // When switching back to dropdown, try to find the current value in voices
            const existingVoice = voices.find((v) => v.voice_id === value);
            if (!existingVoice && voices.length > 0) {
                // If current value not in list, select the first voice
                onChange(voices[0].voice_id);
            }
        }
    };

    const handleManualVoiceIdChange = (newValue: string) => {
        setManualVoiceId(newValue);
        onChange(newValue);
    };

    const getSelectedVoiceName = () => {
        if (isManualInput && value) {
            return value;
        }
        const voice = voices.find((v) => v.voice_id === value);
        return voice?.name || value || "Select a voice";
    };

    const playPreview = (previewUrl: string, voiceId: string) => {
        // Stop current audio if playing
        if (currentAudio) {
            currentAudio.pause();
            currentAudio.currentTime = 0;
            setCurrentAudio(null);
        }

        // If clicking the same voice that's playing, just stop it
        if (playingPreview === voiceId) {
            setPlayingPreview(null);
            return;
        }

        setPlayingPreview(voiceId);
        const audio = new Audio(previewUrl);
        setCurrentAudio(audio);
        audio.onended = () => {
            setPlayingPreview(null);
            setCurrentAudio(null);
        };
        audio.onerror = () => {
            setPlayingPreview(null);
            setCurrentAudio(null);
        };
        audio.play().catch(() => {
            setPlayingPreview(null);
            setCurrentAudio(null);
        });
    };

    // For providers without MPS voice endpoint, show simple input
    if (!hasMPSVoiceEndpoint(provider)) {
        return (
            <div className={cn("space-y-2", className)}>
                <Input
                    type="text"
                    placeholder="Enter voice ID"
                    value={value || ""}
                    onChange={(e) => onChange(e.target.value)}
                />
            </div>
        );
    }

    if (isManualInput && allowManualInput) {
        return (
            <div className={cn("space-y-2", className)}>
                <Input
                    type="text"
                    placeholder="Enter voice ID"
                    value={manualVoiceId}
                    onChange={(e) => handleManualVoiceIdChange(e.target.value)}
                />
                <div className="flex items-center space-x-2">
                    <Checkbox
                        id="manual-voice-input"
                        checked={isManualInput}
                        onCheckedChange={(checked) => handleManualInputToggle(checked as boolean)}
                    />
                    <Label
                        htmlFor="manual-voice-input"
                        className="text-sm font-normal cursor-pointer"
                    >
                        Add Voice ID Manually
                    </Label>
                </div>
            </div>
        );
    }

    return (
        <div className={cn("space-y-2", className)}>
            <Popover open={isOpen} onOpenChange={setIsOpen}>
                <PopoverTrigger asChild>
                    <Button
                        variant="outline"
                        role="combobox"
                        aria-expanded={isOpen}
                        className={cn(
                            "w-full justify-between",
                            !value && "text-muted-foreground"
                        )}
                        disabled={isLoading}
                    >
                        <span className="truncate">
                            {isLoading ? "Loading voices..." : getSelectedVoiceName()}
                        </span>
                        {isLoading ? (
                            <Loader2 className="ml-2 h-4 w-4 shrink-0 animate-spin" />
                        ) : (
                            <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                        )}
                    </Button>
                </PopoverTrigger>
                <PopoverContent className="w-[400px] p-0" align="start">
                    <div className="p-2 space-y-2">
                        <div className="relative">
                            <Search className="absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                            <Input
                                placeholder="Search voices..."
                                value={searchTerm}
                                onChange={(e) => setSearchTerm(e.target.value)}
                                className="pl-8"
                            />
                        </div>

                        {showFilters && (
                            <div className="grid gap-2 sm:grid-cols-3">
                                <Select value={genderFilter} onValueChange={setGenderFilter}>
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Gender" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ALL_FILTER_VALUE}>All genders</SelectItem>
                                        {genderOptions.map((gender) => (
                                            <SelectItem key={gender} value={gender} className="capitalize">
                                                {gender}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>

                                <Select value={languageFilter} onValueChange={setLanguageFilter}>
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Language" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ALL_FILTER_VALUE}>All languages</SelectItem>
                                        {languageOptions.map((voiceLanguage) => (
                                            <SelectItem key={voiceLanguage} value={voiceLanguage} className="uppercase">
                                                {voiceLanguage}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>

                                <Select value={accentFilter} onValueChange={setAccentFilter}>
                                    <SelectTrigger className="h-8">
                                        <SelectValue placeholder="Accent" />
                                    </SelectTrigger>
                                    <SelectContent>
                                        <SelectItem value={ALL_FILTER_VALUE}>All accents</SelectItem>
                                        {accentOptions.map((accent) => (
                                            <SelectItem key={accent} value={accent} className="uppercase">
                                                {accent}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                            </div>
                        )}

                        <div className="max-h-[300px] overflow-auto space-y-1">
                            {error ? (
                                <p className="text-sm text-red-500 text-center py-4">
                                    {error}
                                </p>
                            ) : isLoading ? (
                                <div className="flex items-center justify-center py-4">
                                    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                                </div>
                            ) : filteredVoices.length === 0 ? (
                                <p className="text-sm text-muted-foreground text-center py-4">
                                    No voices found
                                </p>
                            ) : (
                                filteredVoices.map((voice) => (
                                    <div
                                        key={voice.voice_id}
                                        className={cn(
                                            "flex items-start space-x-3 p-2 hover:bg-accent rounded-sm cursor-pointer",
                                            value === voice.voice_id && "bg-accent"
                                        )}
                                        onClick={() => handleSelectVoice(voice.voice_id)}
                                    >
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-2">
                                                <p className="text-sm font-medium truncate">
                                                    {voice.name}
                                                </p>
                                                {voice.gender && (
                                                    <span className="text-xs text-muted-foreground capitalize">
                                                        {voice.gender}
                                                    </span>
                                                )}
                                            </div>
                                            {voice.description && (
                                                <p className="text-xs text-muted-foreground line-clamp-2">
                                                    {voice.description}
                                                </p>
                                            )}
                                            <div className="flex items-center gap-2 mt-1">
                                                {voice.accent && (
                                                    <span className="text-xs bg-secondary px-1.5 py-0.5 rounded capitalize">
                                                        {voice.accent}
                                                    </span>
                                                )}
                                                {voice.language && (
                                                    <span className="text-xs bg-secondary px-1.5 py-0.5 rounded uppercase">
                                                        {voice.language}
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                        {voice.preview_url && (
                                            <Button
                                                variant="ghost"
                                                size="sm"
                                                className="h-8 w-8 p-0 shrink-0"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    playPreview(voice.preview_url!, voice.voice_id);
                                                }}
                                            >
                                                <Volume2
                                                    className={cn(
                                                        "h-4 w-4",
                                                        playingPreview === voice.voice_id &&
                                                            "text-primary animate-pulse"
                                                    )}
                                                />
                                            </Button>
                                        )}
                                    </div>
                                ))
                            )}
                        </div>

                        <div className="pt-2 border-t flex items-center justify-between">
                            {allowManualInput ? (
                                <div className="flex items-center space-x-2">
                                    <Checkbox
                                        id="manual-voice-input-popup"
                                        checked={isManualInput}
                                        onCheckedChange={(checked) => {
                                            handleManualInputToggle(checked as boolean);
                                            if (checked) {
                                                setIsOpen(false);
                                            }
                                        }}
                                    />
                                    <Label
                                        htmlFor="manual-voice-input-popup"
                                        className="text-sm font-normal cursor-pointer"
                                    >
                                        Add Voice ID Manually
                                    </Label>
                                </div>
                            ) : (
                                <span />
                            )}
                            <p className="text-xs text-muted-foreground">
                                {filteredVoices.length} of {voices.length} voices
                            </p>
                        </div>
                    </div>
                </PopoverContent>
            </Popover>
        </div>
    );
};

// ---------------------------------------------------------------------------
// Shared helpers for VoiceSelectorModal (co-located to share the API import)
// ---------------------------------------------------------------------------

const SEARCH_DEBOUNCE_MS = 300;
const DEFAULT_GENDER = "female";
const DEFAULT_ACCENT = "us";
const DEFAULT_LANGUAGE = "en";

interface Facets {
    genders: string[];
    accents: string[];
    languages: string[];
}
const EMPTY_FACETS: Facets = { genders: [], accents: [], languages: [] };

const capitalize = (v: string) => v.charAt(0).toUpperCase() + v.slice(1);
const accentLabel = (code?: string | null) =>
    code ? ACCENT_DISPLAY_NAMES[code.toLowerCase()] || capitalize(code) : "";
const languageLabel = (code?: string | null) =>
    code ? LANGUAGE_DISPLAY_NAMES[code] || code.toUpperCase() : "";
const genderLabel = (gender?: string | null) => (gender ? capitalize(gender) : "");

function voiceTraits(voice: VoiceInfo): string {
    return [accentLabel(voice.accent), genderLabel(voice.gender), languageLabel(voice.language)]
        .filter(Boolean)
        .join(" \u00b7 ");
}

function withSelected(options: string[], selected: string): string[] {
    if (selected === ALL_FILTER_VALUE || options.includes(selected)) return options;
    return [selected, ...options];
}

export interface VoiceSelectorModalProps {
    provider: string;
    value: string;
    onChange: (voiceId: string) => void;
    model?: string;
    allowManualInput?: boolean;
    className?: string;
    /** Typed or saved API key. Masked saved keys are ignored; the backend uses the stored key. */
    apiKey?: string;
}

/**
 * VoiceSelectorModal — full-screen dialog voice picker with server-side
 * filtering (gender / accent / language / search). Shares this file with
 * VoiceSelector (popover variant) to reuse the API import.
 */
function usableApiKey(apiKey?: string): string | undefined {
    if (!apiKey || apiKey.includes("***")) return undefined;
    return apiKey.trim() || undefined;
}

export const VoiceSelectorModal: React.FC<VoiceSelectorModalProps> = ({
    provider,
    value,
    onChange,
    model,
    allowManualInput = false,
    className,
    apiKey,
}) => {
    const [isOpen, setIsOpen] = useState(false);
    const [voices, setVoices] = useState<VoiceInfo[]>([]);
    const [facets, setFacets] = useState<Facets>(EMPTY_FACETS);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const [gender, setGender] = useState(ALL_FILTER_VALUE);
    const [accent, setAccent] = useState(ALL_FILTER_VALUE);
    const [language, setLanguage] = useState(ALL_FILTER_VALUE);
    const [searchInput, setSearchInput] = useState("");
    const [debouncedSearch, setDebouncedSearch] = useState("");

    const [pendingVoiceId, setPendingVoiceId] = useState(value);
    const [selectedVoiceInfo, setSelectedVoiceInfo] = useState<VoiceInfo | null>(null);
    const [manualMode, setManualMode] = useState(false);
    const [manualVoiceId, setManualVoiceId] = useState("");

    const [playingVoiceId, setPlayingVoiceId] = useState<string | null>(null);
    const audioRef = useRef<HTMLAudioElement | null>(null);
    const requestId = useRef(0);

    const stopPreview = useCallback(() => {
        if (audioRef.current) {
            audioRef.current.pause();
            audioRef.current = null;
        }
        setPlayingVoiceId(null);
    }, []);

    useEffect(() => {
        const timer = setTimeout(() => setDebouncedSearch(searchInput), SEARCH_DEBOUNCE_MS);
        return () => clearTimeout(timer);
    }, [searchInput]);

    useEffect(() => {
        if (!value) { setSelectedVoiceInfo(null); return; }
        let active = true;
        (async () => {
            const query: Record<string, string> = { q: value };
            const key = usableApiKey(apiKey);
            if (key) query.api_key = key;
            const response = await getVoicesApiV1UserConfigurationsVoicesProviderGet({
                path: { provider: provider as never },
                query,
            });
            if (!active) return;
            const found = response.data?.voices?.find((v) => v.voice_id === value) ?? null;
            setSelectedVoiceInfo(found);
        })();
        return () => { active = false; };
    }, [value, provider, apiKey]);

    useEffect(() => {
        if (!provider || manualMode) return;
        const id = ++requestId.current;
        setIsLoading(true);
        setError(null);
        (async () => {
            const query: Record<string, string> = {};
            if (model) query.model = model;
            if (isOpen && gender !== ALL_FILTER_VALUE) query.gender = gender;
            if (isOpen && accent !== ALL_FILTER_VALUE) query.accent = accent;
            if (isOpen && language !== ALL_FILTER_VALUE) query.language = language;
            const search = debouncedSearch.trim();
            if (isOpen && search) query.q = search;
            const key = usableApiKey(apiKey);
            if (key) query.api_key = key;
            const response = await getVoicesApiV1UserConfigurationsVoicesProviderGet({
                path: { provider: provider as never },
                query: Object.keys(query).length ? query : undefined,
            });
            if (id !== requestId.current) return;
            if (response.error) {
                const detail = (response.error as { detail?: unknown }).detail;
                setError(typeof detail === "string" ? detail : "Failed to load voices");
                setVoices([]);
            } else {
                setVoices(response.data?.voices ?? []);
                if (response.data?.facets) {
                    setFacets({
                        genders: response.data.facets.genders ?? [],
                        accents: response.data.facets.accents ?? [],
                        languages: response.data.facets.languages ?? [],
                    });
                }
            }
            setIsLoading(false);
        })();
    }, [provider, model, isOpen, manualMode, gender, accent, language, debouncedSearch, apiKey]);

    useEffect(() => {
        if (!isOpen) stopPreview();
        return () => stopPreview();
    }, [isOpen, stopPreview]);

    const toSortedOptions = (codes: string[], selected: string, label: (code: string) => string) =>
        withSelected(codes, selected)
            .map((code) => ({ value: code, label: label(code) }))
            .sort((a, b) => a.label.localeCompare(b.label));

    const genderOptions = useMemo(() => toSortedOptions(facets.genders, gender, genderLabel), [facets.genders, gender]);
    const accentOptions = useMemo(() => toSortedOptions(facets.accents, accent, accentLabel), [facets.accents, accent]);
    const languageOptions = useMemo(() => toSortedOptions(facets.languages, language, languageLabel), [facets.languages, language]);

    const openModal = () => {
        setGender(ALL_FILTER_VALUE); setAccent(ALL_FILTER_VALUE); setLanguage(ALL_FILTER_VALUE);
        setSearchInput(""); setDebouncedSearch(""); setManualMode(false);
        setManualVoiceId(value); setPendingVoiceId(value); setIsOpen(true);
    };

    const playPreview = async (voice: VoiceInfo) => {
        if (playingVoiceId === voice.voice_id) { stopPreview(); return; }
        stopPreview();
        const playUrl = async (url: string) => {
            const audio = new Audio(url);
            audioRef.current = audio;
            setPlayingVoiceId(voice.voice_id);
            const clear = () => {
                if (audioRef.current === audio) audioRef.current = null;
                setPlayingVoiceId((cur) => (cur === voice.voice_id ? null : cur));
            };
            audio.onended = clear; audio.onerror = clear; audio.play().catch(clear);
        };
        if (voice.preview_url) {
            await playUrl(voice.preview_url);
            return;
        }
        try {
            const query: Record<string, string> = { voice_id: voice.voice_id };
            if (model) query.model = model;
            const key = usableApiKey(apiKey);
            if (key) query.api_key = key;
            const response = await client.get({
                url: `/api/v1/user/configurations/voices/${encodeURIComponent(provider)}/preview`,
                query,
                parseAs: "blob",
            });
            if (response.error || !response.data) return;
            await playUrl(URL.createObjectURL(response.data as Blob));
        } catch {
            setPlayingVoiceId(null);
        }
    };

    const commitSelection = () => {
        if (manualMode) {
            const next = manualVoiceId.trim();
            if (next) onChange(next);
        } else if (pendingVoiceId) {
            onChange(pendingVoiceId);
            const chosen = voices.find((v) => v.voice_id === pendingVoiceId);
            if (chosen) setSelectedVoiceInfo(chosen);
        }
        setIsOpen(false);
    };

    const triggerLabel = selectedVoiceInfo?.name || value || "Select a voice";
    const triggerTraits = selectedVoiceInfo ? voiceTraits(selectedVoiceInfo) : "";

    return (
        <div className={cn("space-y-2", className)}>
            <Button type="button" variant="outline"
                className={cn("w-full justify-between", !value && "text-muted-foreground")}
                onClick={openModal}>
                <span className="flex min-w-0 items-center gap-2">
                    <span className="truncate font-medium">
                        {isLoading && !value ? "Loading voices..." : triggerLabel}
                    </span>
                    {triggerTraits && <span className="truncate text-xs text-muted-foreground">{triggerTraits}</span>}
                </span>
                {isLoading ? (
                    <Loader2 className="ml-2 h-4 w-4 shrink-0 animate-spin" />
                ) : (
                    <ChevronDown className="ml-2 h-4 w-4 shrink-0 opacity-50" />
                )}
            </Button>

            <Dialog open={isOpen} onOpenChange={setIsOpen}>
                <DialogContent className="flex max-h-[85vh] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
                    <DialogHeader className="border-b px-6 py-4">
                        <DialogTitle>Select Voice</DialogTitle>
                    </DialogHeader>

                    <div className="flex flex-wrap items-center gap-2 border-b px-6 py-3">
                        <Select value={gender} onValueChange={setGender} disabled={manualMode}>
                            <SelectTrigger className="h-9 w-[130px]"><SelectValue placeholder="Gender" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value={ALL_FILTER_VALUE}>All genders</SelectItem>
                                {genderOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                            </SelectContent>
                        </Select>
                        <Select value={accent} onValueChange={setAccent} disabled={manualMode}>
                            <SelectTrigger className="h-9 w-[140px]"><SelectValue placeholder="Accent" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value={ALL_FILTER_VALUE}>All accents</SelectItem>
                                {accentOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                            </SelectContent>
                        </Select>
                        <Select value={language} onValueChange={setLanguage} disabled={manualMode}>
                            <SelectTrigger className="h-9 w-[150px]"><SelectValue placeholder="Language" /></SelectTrigger>
                            <SelectContent>
                                <SelectItem value={ALL_FILTER_VALUE}>All languages</SelectItem>
                                {languageOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
                            </SelectContent>
                        </Select>
                        <Input placeholder="Search voices..." value={searchInput}
                            onChange={(e) => setSearchInput(e.target.value)}
                            className="h-9 min-w-[160px] flex-1" disabled={manualMode} />
                    </div>

                    <div className="min-h-[260px] flex-1 overflow-auto px-6 py-4">
                        {manualMode ? (
                            <div className="space-y-2">
                                <Label htmlFor="modal-manual-voice-id">Custom voice ID</Label>
                                <Input id="modal-manual-voice-id" placeholder="Enter voice ID"
                                    value={manualVoiceId} onChange={(e) => setManualVoiceId(e.target.value)} autoFocus />
                                <p className="text-xs text-muted-foreground">Use a voice ID that isn&apos;t in the catalog above.</p>
                            </div>
                        ) : error ? (
                            <p className="py-10 text-center text-sm text-destructive">{error}</p>
                        ) : isLoading ? (
                            <div className="flex items-center justify-center py-10">
                                <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                            </div>
                        ) : voices.length === 0 ? (
                            <p className="py-10 text-center text-sm text-muted-foreground">No voices match these filters</p>
                        ) : (
                            <div className="grid gap-2 sm:grid-cols-2">
                                {voices.map((voice) => {
                                    const isSelected = pendingVoiceId === voice.voice_id;
                                    const isPlaying = playingVoiceId === voice.voice_id;
                                    return (
                                        <button type="button" key={voice.voice_id}
                                            onClick={() => setPendingVoiceId(voice.voice_id)}
                                            className={cn(
                                                "flex items-center gap-3 rounded-lg border p-3 text-left transition-colors hover:bg-accent",
                                                isSelected ? "border-primary ring-1 ring-primary" : "border-border",
                                            )}>
                                            <span role="button" tabIndex={0}
                                                aria-label={isPlaying ? "Stop preview" : "Play preview"}
                                                onClick={(e) => { e.stopPropagation(); void playPreview(voice); }}
                                                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); e.stopPropagation(); void playPreview(voice); } }}
                                                className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary hover:bg-primary/20">
                                                {isPlaying ? <Square className="h-4 w-4 fill-current" /> : <Play className="h-4 w-4 fill-current" />}
                                            </span>
                                            <span className="flex min-w-0 flex-1 flex-col">
                                                <span className="flex items-center gap-2">
                                                    <span className="truncate text-sm font-medium">{voice.name}</span>
                                                    {isSelected && <Check className="h-4 w-4 shrink-0 text-primary" />}
                                                </span>
                                                {voiceTraits(voice) && <span className="truncate text-xs text-muted-foreground">{voiceTraits(voice)}</span>}
                                                <span className="truncate text-[11px] text-muted-foreground/70">ID: {voice.voice_id}</span>
                                            </span>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>

                    <div className="flex items-center justify-between gap-3 border-t px-6 py-3">
                        {allowManualInput ? (
                            <Button type="button" variant="ghost" size="sm" className="text-muted-foreground"
                                onClick={() => setManualMode((p) => !p)}>
                                <Pencil className="mr-2 h-4 w-4" />
                                {manualMode ? "Browse catalog" : "Custom voice ID"}
                            </Button>
                        ) : (
                            <span className="text-xs text-muted-foreground">
                                {!manualMode && !isLoading && !error ? `${voices.length} voices` : ""}
                            </span>
                        )}
                        <div className="flex items-center gap-2">
                            <Button type="button" variant="outline" onClick={() => setIsOpen(false)}>Cancel</Button>
                            <Button type="button" onClick={commitSelection}
                                disabled={manualMode ? !manualVoiceId.trim() : !pendingVoiceId}>
                                Use this voice
                            </Button>
                        </div>
                    </div>
                </DialogContent>
            </Dialog>
        </div>
    );
};
