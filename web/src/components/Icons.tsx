// Minimal line-style integration icons. Inline SVG, no icon-font dependency, sized to
// inherit currentColor so they follow the theme without extra props.

import type { ComponentType } from "react";

type IconProps = { size?: number };

export function SlackIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="9" y="2" width="3" height="8" rx="1.5" fill="#36C5F0" />
      <rect x="9" y="14" width="3" height="8" rx="1.5" fill="#2EB67D" />
      <rect x="2" y="9" width="8" height="3" rx="1.5" fill="#ECB22E" />
      <rect x="14" y="9" width="8" height="3" rx="1.5" fill="#E01E5A" />
      <circle cx="17.5" cy="6.5" r="2" fill="#36C5F0" />
      <circle cx="6.5" cy="17.5" r="2" fill="#2EB67D" />
      <circle cx="6.5" cy="6.5" r="2" fill="#ECB22E" />
      <circle cx="17.5" cy="17.5" r="2" fill="#E01E5A" />
    </svg>
  );
}

export function GmailIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24">
      <path fill="#EA4335" d="M2 6.5 12 13l10-6.5V17a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2Z" />
      <path fill="#fff" d="m2 6.5 10 6.5 10-6.5a2 2 0 0 0-2-2H4a2 2 0 0 0-2 2Z" opacity=".16" />
      <path fill="none" stroke="#EA4335" strokeWidth="1.4" strokeLinejoin="round" d="M3 6h18v12a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z" />
      <path fill="none" stroke="#EA4335" strokeWidth="1.4" strokeLinecap="round" d="m3.3 6.3 8.7 6.6 8.7-6.6" />
    </svg>
  );
}

export function DriveIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24">
      <path fill="#0F9D58" d="M8.05 2 1 14.2l3.05 5.3h6.6l-3.05-5.3Z" />
      <path fill="#4285F4" d="M15.95 2H8.05l6.6 12.2h7.3Z" />
      <path fill="#FFCD40" d="m17.6 19.5 3.35-5.3h-7.3l3.35 5.3Z" />
    </svg>
  );
}

export function CallIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 5c0-1.1.9-2 2-2h2.2c.5 0 .9.3 1 .8l1 3.6c.1.4 0 .9-.3 1.2l-1.6 1.6a12.5 12.5 0 0 0 5.5 5.5l1.6-1.6c.3-.3.8-.4 1.2-.3l3.6 1c.5.1.8.5.8 1V18c0 1.1-.9 2-2 2h-1C9.8 20 4 14.2 4 6Z" />
    </svg>
  );
}

export function DirectoryIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 20a5.5 5.5 0 0 1 11 0" />
      <path d="M16 8.5a2.5 2.5 0 1 1 3-2.45" />
      <path d="M15 14.3c2.6.2 4.7 1.9 5.5 4.2" />
    </svg>
  );
}

export function LinearIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <path d="M4 15 15 4M4 19 19 4M9 20 20 9" />
    </svg>
  );
}

export function GithubIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
      <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.46-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.53 2.34 1.09 2.91.83.09-.65.35-1.09.63-1.34-2.22-.25-4.56-1.11-4.56-4.95 0-1.1.39-1.99 1.03-2.69-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.6 9.6 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.69 0 3.85-2.34 4.7-4.57 4.94.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2Z" />
    </svg>
  );
}

export function CrmIcon({ size = 20 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6">
      <rect x="3" y="4" width="18" height="14" rx="2" />
      <path d="M3 9h18M8 4v14" />
    </svg>
  );
}

export const INTEGRATION_ICON: Record<string, ComponentType<IconProps>> = {
  slack: SlackIcon,
  email: GmailIcon,
  drive: DriveIcon,
  call: CallIcon,
  directory: DirectoryIcon,
  linear: LinearIcon,
  github: GithubIcon,
  crm: CrmIcon,
};
