/* Static search registry for the admin Overview's "find a setting" box.
 *
 * The sidebar taxonomy (`adminNav.ts`) gives the search box every PAGE and TAB
 * title for free; this file is what lets an admin type a word that lives
 * INSIDE a page - "cooldown", "timezone", "HIBP", "lockout", "logo" - and land
 * on the page (and, where the page renders an id, the anchor) that holds the
 * control. Each entry names a leaf route, an optional in-page hash, the i18n
 * key of a section heading or field label that already exists in both locale
 * files, and lowercase synonyms an admin might type instead.
 *
 * It is a hand-maintained list, NOT generated. Codegen from the views was
 * considered and rejected for the same reason the repo rejected OpenAPI
 * codegen for `types/api.ts`: the useful part of an entry is the synonym list,
 * which no scanner can produce, and a generator would have to guess which of a
 * page's ~40 keys are controls and which are toasts, help text and errors.
 * The list is pinned two ways instead:
 *  - `tests/config/adminSearchIndex.test.ts` proves every labelKey resolves in
 *    en AND de, every routeName is a sidebar route, hashes are well-formed and
 *    no triple repeats;
 *  - `backend/tests/test_admin_search_index_pin.py` proves the Advanced
 *    section carries exactly one `#tunable-<key>` entry per registry tunable
 *    the Advanced page shows, so a new tunable cannot ship unfindable.
 *
 * Conventions: one entry per line, grouped by page with a comment per page;
 * keywords lowercase, EN and DE mixed freely; a `hash` only where the page
 * actually renders that id (General's `<section id>`s and Advanced's
 * `id="tunable-<key>"` controls - nowhere else today). */

export interface AdminSearchEntry {
  /** Leaf route to navigate to. Must be in ADMIN_ROUTE_NAMES. */
  routeName: string
  /** In-page anchor including '#', only where that page renders the id. */
  hash?: string
  /** i18n key of the label shown in results: a section heading or field label. */
  labelKey: string
  /** Extra lowercase search terms not in the label (synonyms, EN + DE). */
  keywords?: string[]
}

export const ADMIN_SEARCH_INDEX: readonly AdminSearchEntry[] = [
  // --- General (admin-settings-general) - the page renders <section id> anchors
  { routeName: 'admin-settings-general', hash: '#site-url', labelKey: 'admin_site_url.title', keywords: ['domain', 'app url', 'base url', 'public url', 'adresse'] },
  { routeName: 'admin-settings-general', hash: '#site-url', labelKey: 'admin_site_url.url_label', keywords: ['domain', 'app url', 'https', 'email links'] },
  { routeName: 'admin-settings-general', hash: '#site-timezone', labelKey: 'admin_site_timezone.title', keywords: ['zeitzone', '24h', 'time zone', 'clock', 'uhrzeit'] },
  { routeName: 'admin-settings-general', hash: '#site-timezone', labelKey: 'admin_site_timezone.timezone_label', keywords: ['zeitzone', 'iana', 'utc', 'europe/vienna'] },
  { routeName: 'admin-settings-general', hash: '#home-page', labelKey: 'admin_home_page.title', keywords: ['landing', 'welcome', 'startseite', 'root'] },
  { routeName: 'admin-settings-general', hash: '#home-page', labelKey: 'admin_home_page.toggle_label', keywords: ['landing page', 'welcome card', 'startseite'] },
  { routeName: 'admin-settings-general', hash: '#file-preview', labelKey: 'admin_file_preview.title', keywords: ['vorschau', 'pdf', 'inline', 'thumbnail', 'view in browser'] },
  { routeName: 'admin-settings-general', hash: '#file-preview', labelKey: 'admin_file_preview.toggle_label', keywords: ['vorschau', 'preview button', 'inline viewer'] },
  { routeName: 'admin-settings-general', hash: '#share-defaults', labelKey: 'admin_share_defaults.title', keywords: ['notify recipients', 'benachrichtigen', 'default checkbox', 'new share'] },
  { routeName: 'admin-settings-general', hash: '#share-defaults', labelKey: 'admin_share_defaults.toggle_label', keywords: ['notify', 'benachrichtigen', 'share created email'] },
  { routeName: 'admin-settings-general', hash: '#motd', labelKey: 'admin_motd.title', keywords: ['message of the day', 'banner', 'login notice', 'announcement', 'hinweis', 'maintenance notice'] },
  { routeName: 'admin-settings-general', hash: '#motd', labelKey: 'admin_motd.toggle_label', keywords: ['motd', 'banner', 'login page notice'] },
  { routeName: 'admin-settings-general', hash: '#motd', labelKey: 'admin_motd.text_label', keywords: ['motd', 'banner text', 'notice text', 'hinweistext'] },
  { routeName: 'admin-settings-general', hash: '#updates', labelKey: 'admin_updates.title', keywords: ['release', 'version', 'github', 'self-update', 'aktualisierung', 'update check'] },
  { routeName: 'admin-settings-general', hash: '#updates', labelKey: 'admin_updates.url_label', keywords: ['github', 'release api', 'fork', 'update source', 'releases endpoint'] },

  // --- Branding & legal (admin-settings-branding)
  { routeName: 'admin-settings-branding', labelKey: 'admin_branding.logo.title', keywords: ['login page logo', 'brand image', 'upload logo', 'bild', 'png', 'company logo'] },
  { routeName: 'admin-settings-branding', labelKey: 'admin_branding.surfaces.title', keywords: ['where logo appears', 'logo on header', 'logo in emails', 'logo login page', 'desktop client logo'] },
  { routeName: 'admin-settings-branding', labelKey: 'admin_branding.link.label', keywords: ['logo url', 'logo href', 'click logo', 'logo target', 'homepage link'] },
  { routeName: 'admin-settings-branding', labelKey: 'admin_branding.legal.title', keywords: ['imprint', 'privacy policy', 'impressum', 'datenschutz', 'footer', 'legal notice', 'rechtliches'] },
  { routeName: 'admin-settings-branding', labelKey: 'admin_branding.legal.imprint_enable', keywords: ['impressum', 'legal notice', 'imprint page'] },
  { routeName: 'admin-settings-branding', labelKey: 'admin_branding.legal.privacy_enable', keywords: ['datenschutz', 'datenschutzerklärung', 'gdpr', 'dsgvo', 'privacy policy'] },

  // --- SSO providers (admin-settings-sso) - the fields live on the new/edit
  // form; the list is the parameterless route an admin picks a provider from
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.name', keywords: ['sso', 'oidc', 'identity provider', 'idp', 'single sign-on', 'login button', 'openid'] },
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.preset', keywords: ['entra', 'google', 'authentik', 'keycloak', 'azure', 'microsoft', 'sso preset'] },
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.issuer_url', keywords: ['oidc', 'discovery', 'well-known', 'issuer', 'sso url'] },
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.client_id', keywords: ['oidc client id', 'sso client', 'application id'] },
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.client_secret', keywords: ['oidc secret', 'sso secret', 'client secret'] },
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.redirect_uri', keywords: ['callback', 'callback url', 'redirect url', 'oidc callback'] },
  { routeName: 'admin-settings-sso', labelKey: 'admin_sso_edit.enabled', keywords: ['sso enabled', 'show on login page', 'disable sso', 'sso button'] },

  // --- API token policy (admin-settings-api-tokens)
  { routeName: 'admin-settings-api-tokens', labelKey: 'admin_token_policy.mode_label', keywords: ['who can create tokens', 'api token policy', 'token creation', 'api keys', 'api-token richtlinie'] },
  { routeName: 'admin-settings-api-tokens', labelKey: 'admin_token_policy.allowlist_heading', keywords: ['whitelist', 'exception', 'always allowed', 'token allowlist', 'ausnahme'] },
  { routeName: 'admin-settings-api-tokens', labelKey: 'admin_token_policy.users_label', keywords: ['token users', 'allow user tokens', 'specific users'] },
  { routeName: 'admin-settings-api-tokens', labelKey: 'admin_token_policy.groups_label', keywords: ['token groups', 'allow group tokens', 'group allowlist'] },

  // --- Public links (admin-settings-public-links)
  { routeName: 'admin-settings-public-links', labelKey: 'admin_public_link_policy.mode_label', keywords: ['who can create public links', 'öffentliche links', 'anonymous download', 'share link policy', 'link freigabe'] },
  { routeName: 'admin-settings-public-links', labelKey: 'admin_public_link_policy.allowlist_heading', keywords: ['whitelist', 'exception', 'always allowed', 'link allowlist', 'ausnahme'] },
  { routeName: 'admin-settings-public-links', labelKey: 'admin_public_link_policy.users_label', keywords: ['public link users', 'allow user links'] },
  { routeName: 'admin-settings-public-links', labelKey: 'admin_public_link_policy.groups_label', keywords: ['public link groups', 'allow group links'] },

  // --- Share approval / four-eyes (admin-settings-share-approval)
  { routeName: 'admin-settings-share-approval', labelKey: 'admin_share_approval.enable_label', keywords: ['four eyes', 'vier augen', 'freigabe', 'approval', 'review before sending', 'pending approval', 'genehmigung'] },
  { routeName: 'admin-settings-share-approval', labelKey: 'admin_share_approval.mode_label', keywords: ['approvers', 'who approves', 'approver role', 'genehmiger'] },
  { routeName: 'admin-settings-share-approval', labelKey: 'admin_share_approval.allowlist_heading', keywords: ['additional approvers', 'extra approvers', 'approver allowlist'] },
  { routeName: 'admin-settings-share-approval', labelKey: 'admin_share_approval.scope_label', keywords: ['outbound', 'client uploads', 'which shares', 'approval scope', 'inbound shares'] },
  { routeName: 'admin-settings-share-approval', labelKey: 'admin_share_approval.exempt_label', keywords: ['self approval', 'approver own shares', 'skip approval', 'exempt approvers'] },
  { routeName: 'admin-settings-share-approval', labelKey: 'admin_share_approval.review_label', keywords: ['content review', 'preview pending files', 'download pending', 'inspect before approving'] },

  // --- Outgoing mail / SMTP (admin-settings-email)
  { routeName: 'admin-settings-email', labelKey: 'admin_email.host_label', keywords: ['smtp', 'mail server', 'mailserver', 'outgoing mail', 'postausgang', 'smtp host'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.port_label', keywords: ['smtp port', '587', '465', '25'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.helo_label', keywords: ['ehlo', 'helo', 'smtp hostname', 'announce hostname'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.tls_label', keywords: ['starttls', 'ssl', 'implicit tls', 'encryption', 'verschlüsselung', 'smtps'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.user_label', keywords: ['smtp user', 'smtp login', 'benutzername', 'smtp username'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.password_label', keywords: ['smtp password', 'passwort', 'mail password'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.allow_anonymous_label', keywords: ['no auth', 'anonymous smtp', 'relay', 'without credentials', 'unauthenticated'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.from_email_label', keywords: ['sender', 'absender', 'from address', 'reply address', 'sender address'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.from_name_label', keywords: ['sender name', 'absendername', 'display name'] },
  { routeName: 'admin-settings-email', labelKey: 'admin_email.test_heading', keywords: ['test mail', 'send test', 'smtp test', 'testmail', 'test connection'] },

  // --- Inbound mail / IMAP (admin-settings-imap)
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.enable_label', keywords: ['imap', 'inbound', 'incoming mail', 'posteingang', 'fetch mail', 'inbox', 'eingehende mails'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.connection', keywords: ['imap server', 'imap connection', 'mail account'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.use_smtp', keywords: ['reuse smtp credentials', 'same login', 'smtp account for imap'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.host', keywords: ['imap server', 'mailserver', 'imap host', 'incoming server'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.port', keywords: ['imap port', '993', '143'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.tls', keywords: ['imap tls', 'starttls', 'ssl', 'encryption', 'verschlüsselung'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.user', keywords: ['imap user', 'imap login', 'imap username'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.password', keywords: ['imap password', 'mailbox password'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.mailbox', keywords: ['folder', 'inbox', 'ordner', 'postfach', 'imap folder'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.behaviour', keywords: ['after fetch', 'post fetch', 'verhalten', 'imap behaviour'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.post_fetch', keywords: ['mark as read', 'delete from server', 'move to folder', 'after ingest', 'gelesen markieren'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.move_folder', keywords: ['subfolder', 'target folder', 'archive folder', 'zielordner'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.notify', keywords: ['new mail notification', 'inbox notification', 'notify admins', 'human replies'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.require_known_sender', keywords: ['unknown sender', 'anonymous sender', 'spam', 'registered users only', 'unbekannter absender', 'refuse mail'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.tls_insecure', keywords: ['self-signed', 'certificate', 'zertifikat', 'skip verify', 'insecure tls', 'private ca'] },
  { routeName: 'admin-settings-imap', labelKey: 'admin_imap.test', keywords: ['test connection', 'imap test', 'check mailbox'] },

  // --- Email templates (admin-settings-email-templates)
  { routeName: 'admin-settings-email-templates', labelKey: 'admin_email_templates.list_heading', keywords: ['email templates', 'mail templates', 'vorlagen', 'wording', 'customise emails', 'e-mail vorlagen'] },
  { routeName: 'admin-settings-email-templates', labelKey: 'admin_email_templates.subject_label', keywords: ['email subject', 'betreff', 'mail subject', 'subject line'] },
  { routeName: 'admin-settings-email-templates', labelKey: 'admin_email_templates.body_label', keywords: ['email body', 'mail text', 'nachrichtentext', 'email wording', 'template text'] },
  { routeName: 'admin-settings-email-templates', labelKey: 'admin_email_templates.placeholders_heading', keywords: ['variables', 'platzhalter', 'template variables', 'merge fields'] },
  { routeName: 'admin-settings-email-templates', labelKey: 'admin_email_templates.preview_title', keywords: ['preview email', 'render', 'vorschau', 'html preview'] },

  // --- Email change policy (admin-settings-email-change)
  { routeName: 'admin-settings-email-change', labelKey: 'admin_settings_email_change.mode_label', keywords: ['email change', 'change email', 'verify new address', 'e-mail ändern', 'adresse ändern', 'confirm email'] },
  { routeName: 'admin-settings-email-change', labelKey: 'admin_settings_email_change.oidc_label', keywords: ['sso reset', 'email change sso', 'unlink sso', 'set password link', 'sso user email'] },
  { routeName: 'admin-settings-email-change', labelKey: 'admin_settings_email_change.self_service_label', keywords: ['users change own email', 'self service email', 'selbstbedienung', 'own address'] },

  // --- 2FA enforcement (admin-settings-twofa)
  { routeName: 'admin-settings-twofa', labelKey: 'admin_twofa_policy.roles_heading', keywords: ['2fa', 'two factor', 'totp', 'mfa', 'require 2fa', 'zwei-faktor', 'zwei faktor', 'authenticator', 'enforce 2fa', 'passkey'] },
  { routeName: 'admin-settings-twofa', labelKey: 'admin_twofa_policy.groups_heading', keywords: ['2fa groups', 'require 2fa for group', 'mfa', 'zwei-faktor gruppen'] },

  // --- Quarantine alerts & scanner (admin-settings-quarantine)
  { routeName: 'admin-settings-quarantine', labelKey: 'admin_settings_quarantine.toggle_label', keywords: ['virus alert', 'malware', 'infected', 'quarantine email', 'notify admins virus', 'virenfund', 'quarantäne'] },
  { routeName: 'admin-settings-quarantine', labelKey: 'admin_av.title', keywords: ['clamav', 'antivirus', 'virus scanner', 'av', 'clamd', 'virenscanner', 'scanner status'] },
  { routeName: 'admin-settings-quarantine', labelKey: 'admin_av.daemon_label', keywords: ['clamd', 'clamav status', 'antivirus status', 'engine version'] },
  { routeName: 'admin-settings-quarantine', labelKey: 'admin_av.sigs_version_label', keywords: ['virus definitions', 'signatures', 'freshclam', 'signaturen', 'definitions update', 'daily.cvd'] },
  { routeName: 'admin-settings-quarantine', labelKey: 'admin_av.reload_button', keywords: ['reload signatures', 'refresh definitions', 'signaturen neu laden'] },

  // --- Scan guard tab (admin-settings-scan-guard)
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.master_section', keywords: ['scan guard', 'scanner', 'probe', 'auto block', 'automatic blocking', 'fail2ban', 'schutz'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.enabled_label', keywords: ['scanner', 'probe', 'enable scan guard', 'auto ban', 'fail2ban', 'turn on blocking'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.signals_section', keywords: ['signals', 'detection', 'what counts', 'triggers', 'signale'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.probe_path_label', keywords: ['bait', '.env', 'wp-login', 'scanner bait', 'probe', 'config files'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.api_404_label', keywords: ['404', 'unknown api', 'api probing', 'not found'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.auth_failure_label', keywords: ['brute force', 'credential stuffing', 'password guessing', 'failed logins', 'bruteforce', 'anmeldefehler'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.thresholds_section', keywords: ['thresholds', 'how long to block', 'block duration', 'schwellenwerte'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.threshold_label', keywords: ['offences', 'offenses', 'strikes', 'before blocking', 'verstöße'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.window_label', keywords: ['counting window', 'time window', 'zeitfenster', 'window seconds'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.block_minutes_label', keywords: ['block duration', 'first block', 'how long blocked', 'sperrdauer', 'ban length'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.max_block_minutes_label', keywords: ['maximum block', 'longest block', 'max ban', 'block ceiling'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.escalation_label', keywords: ['repeat offenders', 'doubling', 'escalation', 'eskalation', 'wiederholungstäter'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.min_distinct_label', keywords: ['distinct paths', 'path diversity', 'unique paths'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.network_section', keywords: ['cidr', 'range', '/24', 'subnet', 'whole network', 'netzwerk'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.network_label', keywords: ['/24', '/64', 'subnet', 'range block', 'netzwerk sperren', 'block whole range'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.network_threshold_label', keywords: ['addresses in network', 'network escalation threshold', 'blocked per subnet'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.network_lookback_label', keywords: ['lookback', 'network window', 'counted over', 'rückblick'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.v6_prefix_label', keywords: ['ipv6', 'prefix length', '/56', '/64', 'v6 grouping', 'ipv6 subnet'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.paths_section', keywords: ['paths', 'custom probes', 'ignore paths', 'pfade'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.extra_paths_label', keywords: ['custom bait', 'extra paths', 'wp-admin', 'phpmyadmin', 'additional probes'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.ignore_paths_label', keywords: ['exclude paths', 'never count', 'whitelist path', 'ignore', 'ausnahmen pfade'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.watchlist_label', keywords: ['watchlist', 'watching', 'pre-block', 'beobachten', 'beobachtungsliste'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.auth_threshold_label', keywords: ['credential failures', 'login failures before block', 'brute force threshold', 'anmeldefehler schwelle'] },
  { routeName: 'admin-settings-scan-guard', labelKey: 'admin_scan_guard.notify_section', keywords: ['block notification', 'notify on block', 'email on block', 'benachrichtigung', 'every block'] },

  // --- Blocked sources tab (admin-ip-blocks)
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.block_section', keywords: ['ban', 'block ip', 'block address', 'sperren', 'ip sperren', 'manual block', 'cidr', 'blacklist'] },
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.subject_label', keywords: ['ip address', 'cidr', 'range', 'ip adresse', 'subnet'] },
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.minutes_label', keywords: ['block duration', 'how long', 'dauer', 'ban length'] },
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.note_label', keywords: ['reason', 'notiz', 'comment', 'why blocked'] },
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.list_section', keywords: ['blocked ips', 'active blocks', 'banned', 'release block', 'unblock', 'entsperren', 'gesperrt', 'blocked addresses'] },
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.allowlist_section', keywords: ['allowlist', 'whitelist', 'never block', 'exempt', 'trusted ip', 'ausnahme', 'office ip'] },
  { routeName: 'admin-ip-blocks', labelKey: 'admin_ip_blocks.watch_section', keywords: ['watchlist', 'watching', 'suspicious', 'offences building up', 'beobachtet', 'not yet blocked'] },

  // --- Errors & alerts › Alerts tab (admin-settings-error-alerts)
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.log_section', keywords: ['error log', '5xx', 'server errors', 'fehlerprotokoll', 'fehler'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.log_enabled_label', keywords: ['record errors', 'log errors', 'error logging', 'fehler aufzeichnen'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.capture_4xx_label', keywords: ['4xx', '404', 'client errors', 'capture 404', 'scanner detection', 'status codes'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.retention_label', keywords: ['error log retention', 'keep errors', 'prune errors', 'aufbewahrung', 'error history'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.alert_section', keywords: ['error emails', 'email alerts', 'alerting', 'benachrichtigung fehler', 'ops alert', 'alarm'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.enabled_label', keywords: ['email on error', 'alert emails', 'error notification', 'fehler e-mail'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.http_label', keywords: ['5xx', '500', 'server error alert', 'internal server error'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.http_4xx_label', keywords: ['4xx alert', '404 email', 'client error email'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.worker_label', keywords: ['cron failure', 'failed cron', 'background task failed', 'scheduled task alert', 'worker error', 'job failed'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.recipients_label', keywords: ['who gets alerts', 'alert email address', 'ops email', 'empfänger', 'sre'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.throttle_label', keywords: ['flood', 'throttle', 'too many emails', 'rate limit alerts', 'anti-flood', 'drosselung'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.cooldown_label', keywords: ['flood', 'throttle', 'too many emails', 'dedup', 'cooldown minutes', 'abkühlung', 'repeat alert'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_error_alerts.cap_label', keywords: ['max emails per hour', 'email cap', 'flood', 'hourly cap', 'alert limit'] },

  // --- Maintenance mode (admin-settings-maintenance)
  { routeName: 'admin-settings-maintenance', labelKey: 'admin_maintenance.toggle_label', keywords: ['maintenance mode', 'wartung', 'wartungsmodus', 'pause uploads', '503', 'downtime', 'read only', 'pause transfers'] },
  { routeName: 'admin-settings-maintenance', labelKey: 'admin_maintenance.message_label', keywords: ['maintenance banner', 'wartungshinweis', 'banner text', 'downtime message'] },

  // --- Configuration backup (admin-settings-backup)
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.export_title', keywords: ['backup', 'export config', 'download configuration', 'sicherung', 'disaster recovery', 'fhbackup', 'export settings'] },
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.categories_label', keywords: ['what to back up', 'backup scope', 'include users', 'backup categories', 'umfang'] },
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.secret_label', keywords: ['passphrase', 'encrypt backup', 'ciphertext', 'exclude secrets', 'secret handling', 'geheimnisse'] },
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.passphrase_label', keywords: ['backup password', 'encrypt', 'passphrase', 'backup passwort'] },
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.include_env', keywords: ['env snapshot', '.env', 'environment variables', 'jwt_secret', 'infrastructure secrets'] },
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.import_title', keywords: ['restore', 'import config', 'wiederherstellen', 'upload backup', 'replace configuration', 'import settings'] },
  { routeName: 'admin-settings-backup', labelKey: 'admin_backup.import_file_label', keywords: ['restore file', 'fhbackup', 'upload backup', 'backup datei'] },

  // --- Webhooks (admin-settings-webhooks)
  { routeName: 'admin-settings-webhooks', labelKey: 'admin_webhooks.add', keywords: ['webhook', 'http callback', 'slack', 'integration', 'post events', 'outbound hook'] },
  { routeName: 'admin-settings-webhooks', labelKey: 'admin_webhooks.name', keywords: ['webhook name', 'hook label'] },
  { routeName: 'admin-settings-webhooks', labelKey: 'admin_webhooks.url', keywords: ['webhook url', 'endpoint', 'callback url', 'ziel-url', 'target url'] },
  { routeName: 'admin-settings-webhooks', labelKey: 'admin_webhooks.events', keywords: ['webhook events', 'which events', 'triggers', 'ereignisse', 'subscribe events'] },
  { routeName: 'admin-settings-webhooks', labelKey: 'admin_webhooks.secret_created', keywords: ['signing secret', 'hmac', 'x-webhook-signature', 'webhook secret', 'signature'] },
  { routeName: 'admin-settings-webhooks', labelKey: 'admin_webhooks.deliveries', keywords: ['webhook deliveries', 'delivery log', 'retry', 'failed deliveries', 'zustellungen', 'webhook history'] },

  // --- Scheduled tasks (admin-scheduled-tasks) - column/heading keys only,
  // the task names themselves come from the API
  { routeName: 'admin-scheduled-tasks', labelKey: 'admin_scheduled_tasks.col_schedule', keywords: ['cron', 'cadence', 'interval', 'how often', 'zeitplan', 'daily at', 'background jobs'] },
  { routeName: 'admin-scheduled-tasks', labelKey: 'admin_scheduled_tasks.alert_on_failure', keywords: ['cron failure email', 'task failed alert', 'alert on failure', 'fehler benachrichtigen', 'per task alert'] },
  { routeName: 'admin-scheduled-tasks', labelKey: 'admin_scheduled_tasks.run_now', keywords: ['trigger job', 'run job', 'manually run', 'jetzt ausführen', 'force run'] },
  { routeName: 'admin-scheduled-tasks', labelKey: 'admin_scheduled_tasks.kind_daily', keywords: ['daily at', 'pinned time', 'uhrzeit', 'nightly'] },

  // --- Registry tunables: each row points at the page that RENDERS it
  //     (config/adminTunablePlacement.ts), not at the endpoint that stores it.
  //     Group headings first, no hash
  { routeName: 'admin-settings-sessions', labelKey: 'admin_advanced.groups.sessions', keywords: ['session', 'jwt', 'token lifetime', 'sitzung', 'login duration', 'authentication'] },
  { routeName: 'admin-settings-sign-in', labelKey: 'admin_advanced.groups.rate_limits', keywords: ['lockout', 'locked out', 'gesperrt', 'brute force', 'rate limit', 'throttle', 'kontosperre'] },
  { routeName: 'admin-settings-advanced', labelKey: 'admin_advanced.groups.retention', keywords: ['retention', 'prune', 'purge', 'keep for', 'aufbewahrung', 'löschen nach', 'cleanup', 'how long kept'] },
  { routeName: 'admin-settings-transfers', labelKey: 'admin_advanced.groups.uploads', keywords: ['upload limit', 'max upload', 'direct upload', 'upload size'] },
  { routeName: 'admin-settings-transfers', labelKey: 'admin_advanced.groups.downloads', keywords: ['download link', 'signed url', 'resume', 'download settings'] },
  { routeName: 'admin-settings-sign-in', labelKey: 'admin_advanced.groups.security', keywords: ['hibp', 'pwned', 'password check', 'sicherheit'] },
  { routeName: 'admin-settings-branding', labelKey: 'admin_advanced.groups.branding', keywords: ['app name', 'brand name', 'product name', 'anwendungsname'] },
  { routeName: 'admin-settings-advanced', labelKey: 'admin_advanced.groups.storage', keywords: ['disk space', 'low disk', 'speicherplatz', 'free space', 'disk full', 'storage warning'] },
  { routeName: 'admin-settings-anomaly', labelKey: 'admin_advanced.groups.anomaly', keywords: ['anomaly', 'suspicious activity', 'mass download', 'impossible travel', 'anomalie'] },
  { routeName: 'admin-system', labelKey: 'admin_advanced.groups.updates', keywords: ['drain', 'postpone update', 'update wait', 'self-update'] },
  { routeName: 'admin-settings-error-alerts', labelKey: 'admin_advanced.groups.error_alert', keywords: ['cooldown', 'flood', 'error email cap', '4xx capture rate', 'alert throttle'] },

  // --- Advanced (admin-settings-advanced): one entry per tunable the page
  // shows, hash = the control's id. Pinned against
  // `settings_registry.TUNABLES` minus `_MANAGED_ELSEWHERE_GROUPS` by
  // backend/tests/test_admin_search_index_pin.py.
  { routeName: 'admin-settings-sessions', hash: '#tunable-auth.access_token_expire_minutes', labelKey: 'admin_advanced.keys.auth.access_token_expire_minutes', keywords: ['jwt', 'access token', 'session length', 'token ttl', 'sitzung', 'revoke window'] },
  { routeName: 'admin-settings-sessions', hash: '#tunable-auth.refresh_token_expire_days', labelKey: 'admin_advanced.keys.auth.refresh_token_expire_days', keywords: ['refresh token', 'stay signed in', 'remember me', 'session expiry', 'sitzungsdauer'] },
  { routeName: 'admin-settings-sessions', hash: '#tunable-auth.max_active_sessions_per_user', labelKey: 'admin_advanced.keys.auth.max_active_sessions_per_user', keywords: ['session cap', 'concurrent sessions', 'devices', 'max sessions', 'geräte'] },
  { routeName: 'admin-settings-sign-in', hash: '#tunable-rate_limit.login', labelKey: 'admin_advanced.keys.rate_limit.login', keywords: ['login attempts', 'per ip', 'rate limit', 'anmeldeversuche', '429'] },
  { routeName: 'admin-settings-sign-in', hash: '#tunable-rate_limit.register', labelKey: 'admin_advanced.keys.rate_limit.register', keywords: ['register limit', 'forgot password limit', 'invite rate limit', 'reset limit'] },
  { routeName: 'admin-settings-sign-in', hash: '#tunable-rate_limit.login_window_sec', labelKey: 'admin_advanced.keys.rate_limit.login_window_sec', keywords: ['rate limit window', 'sliding window', 'zeitfenster'] },
  { routeName: 'admin-settings-sign-in', hash: '#tunable-rate_limit.lockout_threshold', labelKey: 'admin_advanced.keys.rate_limit.lockout_threshold', keywords: ['lockout', 'locked out', 'gesperrt', 'failed logins', 'account lock', 'kontosperre'] },
  { routeName: 'admin-settings-sign-in', hash: '#tunable-rate_limit.lockout_duration_min', labelKey: 'admin_advanced.keys.rate_limit.lockout_duration_min', keywords: ['lockout', 'locked out', 'gesperrt', 'lock duration', 'sperrdauer', 'unlock'] },
  { routeName: 'admin-settings-public-links', hash: '#tunable-public_link.password_rate_limit', labelKey: 'admin_advanced.keys.public_link.password_rate_limit', keywords: ['public link password attempts', 'link password guessing', 'brute force link'] },
  { routeName: 'admin-settings-public-links', hash: '#tunable-public_link.password_window_sec', labelKey: 'admin_advanced.keys.public_link.password_window_sec', keywords: ['public link password window', 'link attempts window'] },
  { routeName: 'admin-settings-public-links', hash: '#tunable-public_link.lockout_sec', labelKey: 'admin_advanced.keys.public_link.lockout_sec', keywords: ['public link lock', 'link locked', 'link lockout', 'link gesperrt'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.refresh_token_days', labelKey: 'admin_advanced.keys.retention.refresh_token_days', keywords: ['refresh token cleanup', 'old sessions', 'session rows', 'revoked tokens'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.invite_days', labelKey: 'admin_advanced.keys.retention.invite_days', keywords: ['invite expiry', 'pending invites', 'einladung', 'unused invites'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.audit_log_days', labelKey: 'admin_advanced.keys.retention.audit_log_days', keywords: ['audit retention', 'audit log', 'prune audit', 'protokoll', 'audit trail'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.download_log_days', labelKey: 'admin_advanced.keys.retention.download_log_days', keywords: ['download log', 'download history', 'download retention'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.email_log_days', labelKey: 'admin_advanced.keys.retention.email_log_days', keywords: ['mail log', 'email log retention', 'mail history', 'sent mail'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.inbound_message_days', labelKey: 'admin_advanced.keys.retention.inbound_message_days', keywords: ['imap retention', 'inbox retention', 'inbound mail cleanup', 'posteingang'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.login_attempt_days', labelKey: 'admin_advanced.keys.retention.login_attempt_days', keywords: ['login attempts', 'login history', 'forensics', 'anmeldeversuche'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.webhook_delivery_days', labelKey: 'admin_advanced.keys.retention.webhook_delivery_days', keywords: ['webhook deliveries', 'webhook history', 'delivery retention'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.public_link_attempt_days', labelKey: 'admin_advanced.keys.retention.public_link_attempt_days', keywords: ['public link attempts', 'password attempts log', 'link attempt history'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.ip_block_days', labelKey: 'admin_advanced.keys.retention.ip_block_days', keywords: ['ip block history', 'old blocks', 'block retention', 'released blocks'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.notification_read_days', labelKey: 'admin_advanced.keys.retention.notification_read_days', keywords: ['notifications cleanup', 'read notifications', 'bell', 'benachrichtigungen'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.quarantine_purge_days', labelKey: 'admin_advanced.keys.retention.quarantine_purge_days', keywords: ['quarantine purge', 'infected bytes', 'quarantäne löschen', 'malware retention'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.orphan_reclaim_days', labelKey: 'admin_advanced.keys.retention.orphan_reclaim_days', keywords: ['orphaned files', 'reclaim', 'revoked share bytes', 'disk cleanup', 'verwaiste dateien'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.tus_abandoned_hours', labelKey: 'admin_advanced.keys.retention.tus_abandoned_hours', keywords: ['abandoned upload', 'tus', 'staging cleanup', 'temp upload'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-retention.upload_stale_hours', labelKey: 'admin_advanced.keys.retention.upload_stale_hours', keywords: ['stale upload', 'stuck upload', 'inactive upload', 'reaper', 'hängender upload'] },
  { routeName: 'admin-settings-transfers', hash: '#tunable-uploads.max_direct_bytes', labelKey: 'admin_advanced.keys.uploads.max_direct_bytes', keywords: ['upload limit', 'max upload', 'direct upload', '100 mb', 'multipart', 'upload size', 'uploadgröße'] },
  { routeName: 'admin-settings-transfers', hash: '#tunable-downloads.signed_url_ttl_sec', labelKey: 'admin_advanced.keys.downloads.signed_url_ttl_sec', keywords: ['download link expiry', 'signed url', 'download url lifetime', 'link ttl', 'resume'] },
  { routeName: 'admin-settings-transfers', hash: '#tunable-downloads.resume_credit_hours', labelKey: 'admin_advanced.keys.downloads.resume_credit_hours', keywords: ['resume', 'partial download', 'range', 'download budget', 'continue download', 'fortsetzen'] },
  { routeName: 'admin-system', hash: '#tunable-updates.drain_max_wait_min', labelKey: 'admin_advanced.keys.updates.drain_max_wait_min', keywords: ['drain', 'postpone update', 'wait for transfers', 'maintenance wait', 'update delay'] },
  { routeName: 'admin-system', hash: '#tunable-updates.backup_default', labelKey: 'admin_advanced.keys.updates.backup_default', keywords: ['backup before update', 'pre-update backup', 'update checkbox', 'database backup'] },
  { routeName: 'admin-system', hash: '#tunable-updates.backup_on_db_change', labelKey: 'admin_advanced.keys.updates.backup_on_db_change', keywords: ['database upgrade', 'mariadb upgrade', 'forced backup', 'pre-update backup'] },
  { routeName: 'admin-system', hash: '#tunable-updates.backup_keep', labelKey: 'admin_advanced.keys.updates.backup_keep', keywords: ['backup retention', 'keep backups', 'pre-update backups', 'prune backups'] },
  { routeName: 'admin-system', hash: '#tunable-updates.backup_max_age_days', labelKey: 'admin_advanced.keys.updates.backup_max_age_days', keywords: ['backup retention', 'backup age', 'delete old backups', 'pre-update backups'] },
  { routeName: 'admin-system', hash: '#tunable-updates.infra_sync', labelKey: 'admin_advanced.keys.updates.infra_sync', keywords: ['infra sync', 'mariadb', 'redis', 'clamav', 'tusd', 'host step', 'git pull'] },
  { routeName: 'admin-settings-sign-in', hash: '#tunable-security.hibp_enabled', labelKey: 'admin_advanced.keys.security.hibp_enabled', keywords: ['hibp', 'pwned', 'breach', 'leaked password', 'have i been pwned', 'password check', 'kompromittiert'] },
  { routeName: 'admin-settings-branding', hash: '#tunable-branding.app_name', labelKey: 'admin_advanced.keys.branding.app_name', keywords: ['app name', 'brand name', 'product name', 'anwendungsname', 'title'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-storage.low_threshold_percent', labelKey: 'admin_advanced.keys.storage.low_threshold_percent', keywords: ['disk space', 'low disk', 'free space percent', 'speicherplatz', 'disk warning'] },
  { routeName: 'admin-settings-advanced', hash: '#tunable-storage.low_threshold_bytes', labelKey: 'admin_advanced.keys.storage.low_threshold_bytes', keywords: ['disk space', 'low disk', 'free bytes', 'speicherplatz', 'disk warning'] },
  { routeName: 'admin-settings-anomaly', hash: '#tunable-anomaly.enabled', labelKey: 'admin_advanced.keys.anomaly.enabled', keywords: ['anomaly', 'anomalie', 'suspicious', 'heuristics', 'detection'] },
  { routeName: 'admin-settings-anomaly', hash: '#tunable-anomaly.mass_download_threshold', labelKey: 'admin_advanced.keys.anomaly.mass_download_threshold', keywords: ['mass download', 'bulk download', 'exfiltration', 'massendownload'] },
  { routeName: 'admin-settings-anomaly', hash: '#tunable-anomaly.multi_network_threshold', labelKey: 'admin_advanced.keys.anomaly.multi_network_threshold', keywords: ['impossible travel', 'multiple networks', 'account sharing', 'distinct networks'] },
  { routeName: 'admin-settings-anomaly', hash: '#tunable-anomaly.login_failure_threshold', labelKey: 'admin_advanced.keys.anomaly.login_failure_threshold', keywords: ['login failures', 'credential stuffing', 'brute force alert', 'anmeldefehler'] },
  { routeName: 'admin-settings-error-alerts', hash: '#tunable-error_log.scan_capture_per_min', labelKey: 'admin_advanced.keys.error_log.scan_capture_per_min', keywords: ['4xx capture', 'scan capture', '404 logging rate', 'scanner logging'] },
]
