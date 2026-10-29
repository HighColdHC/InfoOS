export type PlatformSourceTypeCapability = {
 source_type: string;
 initial_status: string;
 default_cadence: string;
 requires_login?: "required" | "optional" | "no" | string;
 login_verifiable?: "verifiable" | "n/a" | string;
};

export type PlatformCapability = {
 platform: string;
 label: string;
 source_types: PlatformSourceTypeCapability[];
 cadence_options: number[];
 requires_login: "required" | "optional" | "no" | string;
 login_verifiable: "yes" | "no" | "n/a" | string;
 requires_browser_session: boolean;
 configurable: boolean;
};
