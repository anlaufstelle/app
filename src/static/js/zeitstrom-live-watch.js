/* Conditional multi-user refresh for the Zeitstrom (#1916).
 *
 * Validators live only in this closure. The server receives the canonical
 * five-field state from #zeitstrom-filter-state and opaque component
 * validators in a request header; URLs, history, and storage stay clean.
 */
(function () {
    "use strict";

    const BASE_DELAY = 12_000;
    const MAX_DELAY = 60_000;
    const FILTER_KEYS = ["date", "time_filter", "type", "doc_type", "view"];
    const WATCH_HEADER = "X-Zeitstrom-Watch";
    const VALIDATORS_HEADER = "X-Zeitstrom-Validators";
    const PAGE_SCOPE_HEADER = "X-Zeitstrom-Page-Scope";
    const FILTER_TRANSITION_HEADER = "X-Zeitstrom-Filter-Transition";
    const PREVIOUS_STATE_HEADER = "X-Zeitstrom-Previous-State";
    const WATCH_COMPONENTS = new Set([
        "feed",
        "tally_counts",
        "tally_topology",
        "current_shift",
        "focus_box",
        "bans",
        "handover_note",
    ]);

    function defaultNavigate(url) {
        window.location.assign(url);
    }

    // Refs #1951: Vertrag mit sw-register.js — ``window.__logoutInFlight`` ist
    // der Zeitstempel (ms), zu dem die Logout-Navigation abgeschickt wurde.
    // Das Fenster ist bewusst identisch zu ``_LOGOUT_NAV_GRACE_MS`` dort: ohne
    // Deckel bliebe ein Dokument, dessen Logout nie navigiert hat, dauerhaft
    // blind fuer einen spaeteren Session-Ablauf.
    const LOGOUT_NAV_GRACE_MS = 10_000;

    function logoutNavInFlight() {
        const startedAt = window.__logoutInFlight;
        return typeof startedAt === "number" && Date.now() - startedAt < LOGOUT_NAV_GRACE_MS;
    }

    // Refs #2352: quick-exit.js setzt diese Marke beim Schnell-Verlassen und
    // meldet ``quick-exit:leaving``. Anders als die Logout-Marke ist sie
    // unbefristet: das Dokument wird verlassen, egal wie lange die Zielseite
    // laedt — kein Poll mehr und keine eigene Navigation nach /login/.
    function quickExitLeaving() {
        return window.__quickExitLeaving === true;
    }

    function createZeitstromLiveWatch(root, options) {
        options = options || {};
        const fetchRequest = options.fetch || window.fetch.bind(window);
        const random = options.random || Math.random;
        const navigate = options.navigate || defaultNavigate;

        let generation = 0;
        let validators = {};
        if (root.dataset.tallyTopology) validators.tally_topology = root.dataset.tallyTopology;
        let pageEtag = "";
        let failures = 0;
        let timer = null;
        let cycle = null;
        let started = false;
        let stopped = false;
        let pageActive = true;
        let pendingFilterTransition = null;
        let abortedTransitionBaseline = null;

        // The first request carries the server-rendered topology baseline.
        // Regions whose full render cannot share one DB snapshot
        // perform one targeted baseline round. This bounded cost closes the
        // render-to-first-poll race without ever swapping the page or tally
        // rows; later cycles request only actually changed regions.

        function stateParams() {
            const params = new URLSearchParams();
            FILTER_KEYS.forEach(function (name) {
                const input = document.querySelector('#zeitstrom-filter-state [name="' + name + '"]');
                params.set(name, input ? input.value : "");
            });
            return params;
        }

        function stateSignature(parameters) {
            const snapshot = new URLSearchParams();
            for (const name of FILTER_KEYS) {
                let value;
                if (parameters && typeof parameters.get === "function") value = parameters.get(name);
                else value = parameters ? parameters[name] : undefined;
                if (value === undefined || value === null || Array.isArray(value)) return null;
                snapshot.set(name, String(value));
            }
            return snapshot.toString();
        }

        function stateObject(parameters) {
            const state = {};
            for (const name of FILTER_KEYS) {
                const value = parameters ? parameters[name] : undefined;
                if (typeof value !== "string") return null;
                state[name] = value;
            }
            return Object.keys(parameters).length === FILTER_KEYS.length ? state : null;
        }

        function urlWithState(baseUrl, extra) {
            const url = new URL(baseUrl, window.location.origin);
            stateParams().forEach(function (value, name) {
                url.searchParams.set(name, value);
            });
            Object.entries(extra || {}).forEach(function ([name, value]) {
                url.searchParams.set(name, value);
            });
            return url.toString();
        }

        function canPoll() {
            return (
                !stopped &&
                !quickExitLeaving() &&
                pageActive &&
                document.visibilityState === "visible" &&
                navigator.onLine !== false
            );
        }

        function clearTimer() {
            if (timer !== null) {
                window.clearTimeout(timer);
                timer = null;
            }
        }

        function nextDelay() {
            if (failures === 0) return BASE_DELAY;
            const exponential = Math.min(MAX_DELAY, BASE_DELAY * Math.pow(2, failures));
            const jitter = 0.8 + random() * 0.4;
            return Math.min(MAX_DELAY, Math.round(exponential * jitter));
        }

        function schedule() {
            clearTimer();
            if (!started || !canPoll() || cycle !== null) return;
            timer = window.setTimeout(function () {
                timer = null;
                poll();
            }, nextDelay());
        }

        function abortCycle(invalidate) {
            if (invalidate) generation += 1;
            if (cycle) cycle.controller.abort();
            cycle = null;
        }

        function pause() {
            clearTimer();
            abortCycle(true);
        }

        function stop() {
            stopped = true;
            clearTimer();
            abortCycle(true);
        }

        function leaveSensitivePage(url) {
            stop();
            root.replaceChildren();
            // Refs #1951: Laeuft schon ein Logout, ist die Navigation nach
            // ``/login/`` bereits unterwegs (sw-register.js setzt die Marke
            // unmittelbar bevor es die Logout-Form abschickt). Ein zweites
            // ``location.assign`` wuerde diese Navigation ABBRECHEN
            // (``net::ERR_ABORTED``) und die alte, jetzt unauthentifizierte URL
            // neu laden — der Nutzer landete auf ``/login/?next=<alte URL>``,
            // als ``next`` sogar mit dieser hier gerade umgeleiteten
            // Partial-URL. Der schutzrelevante Teil ist oben schon passiert
            // (Polling aus, sensible Inhalte raus); die Navigation selbst
            // erledigt der Logout.
            if (quickExitLeaving() || logoutNavInFlight()) return;
            navigate(url);
        }

        async function authOrHtmlResponse(response) {
            if (response.redirected) {
                leaveSensitivePage(response.url || "/login/");
                return true;
            }
            const hxRedirect = response.headers.get("HX-Redirect");
            if (hxRedirect) {
                leaveSensitivePage(hxRedirect);
                return true;
            }
            if (response.status === 401) {
                let loginUrl = "/login/";
                const contentType = response.headers.get("Content-Type") || "";
                if (contentType.includes("application/json")) {
                    try {
                        const payload = await response.json();
                        if (payload && payload.login_url) loginUrl = payload.login_url;
                    } catch (_error) {
                        // The navigation below is the fail-closed fallback.
                    }
                }
                leaveSensitivePage(loginUrl);
                return true;
            }
            if (response.status === 403 || response.status === 409) {
                leaveSensitivePage(window.location.href);
                return true;
            }
            return false;
        }

        function snapshotMismatch() {
            const error = new Error("ZeitstromSnapshotMismatch");
            error.name = "ZeitstromSnapshotMismatch";
            return error;
        }

        function followupHeaders(expectedValidator) {
            return {
                Accept: "text/html",
                "HX-Request": "true",
                "If-Match": expectedValidator,
                [WATCH_HEADER]: "1",
                [PAGE_SCOPE_HEADER]: root.dataset.pageScope || "",
            };
        }

        function isLoginDocument(html) {
            return /<(?:html|body)\b/i.test(html) && /(?:<form\b[^>]*(?:login|anmeld)|\/login\/|anmelden)/i.test(html);
        }

        async function htmlFollowup(baseUrl, signal, expectedValidator) {
            const response = await fetchRequest(urlWithState(baseUrl), {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                redirect: "follow",
                signal: signal,
                headers: followupHeaders(expectedValidator),
            });
            if (await authOrHtmlResponse(response)) return null;
            if (response.status === 412) throw snapshotMismatch();
            if (!response.ok) throw new Error("ZeitstromFollowupHTTP" + response.status);
            const contentType = response.headers.get("Content-Type") || "";
            if (!contentType.includes("text/html")) throw new Error("ZeitstromFollowupContentType");
            const html = await response.text();
            if (response.headers.get("ETag") !== expectedValidator) {
                if (isLoginDocument(html)) {
                    leaveSensitivePage("/login/");
                    return null;
                }
                throw snapshotMismatch();
            }
            return html;
        }

        function swapRegion(target, html, swapStyle) {
            const scrollX = window.scrollX;
            const scrollY = window.scrollY;
            if (window.htmx && typeof window.htmx.swap === "function") {
                // Use HTMX's normal insertion pipeline so hx-* attributes are
                // initialized and Alpine's mutation observer sees the same
                // DOM lifecycle as an ordinary HTMX response.
                window.htmx.swap(target, html, { swapStyle: swapStyle, settleDelay: 0 });
            } else if (swapStyle === "outerHTML") {
                const template = document.createElement("template");
                template.innerHTML = html.trim();
                const fresh = template.content.firstElementChild;
                if (fresh) target.replaceWith(fresh);
            } else {
                target.innerHTML = html;
            }
            // A background refresh must never move the reader. HTMX itself
            // does not request scrolling here, but extensions or focus
            // restoration may do so during a swap.
            if ((window.scrollX !== scrollX || window.scrollY !== scrollY) && window.scrollTo) {
                window.scrollTo(scrollX, scrollY);
            }
        }

        function preserveFeedCardState(target, html) {
            const states = new Map();
            target
                .querySelectorAll(
                    'button[aria-controls^="event-detail-"], button[aria-controls^="activity-detail-"]',
                )
                .forEach(function (button) {
                    states.set(
                        button.getAttribute("aria-controls"),
                        button.getAttribute("aria-expanded") === "true",
                    );
                });
            if (states.size === 0) return html;

            const template = document.createElement("template");
            template.innerHTML = html;
            template.content
                .querySelectorAll(
                    'button[aria-controls^="event-detail-"], button[aria-controls^="activity-detail-"]',
                )
                .forEach(function (button) {
                    const controls = button.getAttribute("aria-controls");
                    if (!states.has(controls)) return;
                    const card = button.closest(
                        '[x-data="expandableCard"], [x-data="expandableActivityCard"]',
                    );
                    if (card) card.dataset.zeitstromExpanded = String(states.get(controls));
                });
            return template.innerHTML;
        }

        async function refreshFeed(signal, expectedGeneration, expectedValidator) {
            const response = await fetchRequest(urlWithState(root.dataset.feedUrl, { feed_only: "1" }), {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                redirect: "follow",
                signal: signal,
                headers: followupHeaders(expectedValidator),
            });
            if (await authOrHtmlResponse(response)) return;
            if (response.status === 412) throw snapshotMismatch();
            if (!response.ok) throw new Error("ZeitstromFeedHTTP" + response.status);
            const contentType = response.headers.get("Content-Type") || "";
            if (!contentType.includes("text/html")) throw new Error("ZeitstromFeedContentType");
            const html = await response.text();
            if (response.headers.get("ETag") !== expectedValidator) {
                if (isLoginDocument(html)) {
                    leaveSensitivePage("/login/");
                    return;
                }
                throw snapshotMismatch();
            }
            if (expectedGeneration !== generation || signal.aborted) return;
            const target = document.getElementById("feed-list");
            if (target) swapRegion(target, preserveFeedCardState(target, html), "innerHTML");
        }

        async function refreshTallyCounts(signal, expectedGeneration, expectedValidator) {
            const html = await htmlFollowup(root.dataset.tallyCountsUrl, signal, expectedValidator);
            if (html === null || expectedGeneration !== generation || signal.aborted) return;
            const template = document.createElement("template");
            template.innerHTML = html;
            template.content.querySelectorAll('[id^="tally-count-"][data-confirmed-count]').forEach(function (fresh) {
                const current = document.getElementById(fresh.id);
                if (!current) return;
                current.textContent = fresh.textContent;
                current.dataset.confirmedCount = fresh.dataset.confirmedCount;
                current.setAttribute("aria-label", fresh.getAttribute("aria-label") || "");
            });
            window.dispatchEvent(new CustomEvent("zeitstrom:tally-counts-updated"));
        }

        function handoverFormIsOpen(target) {
            return Array.from(target.querySelectorAll("form")).some(function (form) {
                if (form.contains(document.activeElement)) return true;
                return Array.from(form.querySelectorAll("textarea")).some(function (field) {
                    return field.value !== field.defaultValue;
                });
            });
        }

        async function refreshHandoverNote(signal, expectedGeneration, expectedValidator) {
            const current = document.getElementById("handover-note");
            if (!current || handoverFormIsOpen(current)) return false;
            const html = await htmlFollowup(root.dataset.handoverNoteUrl, signal, expectedValidator);
            if (html === null || expectedGeneration !== generation || signal.aborted) return false;
            // The user may have focused or changed the form while the request
            // was in flight. The late response must not destroy that work.
            if (handoverFormIsOpen(current)) return false;
            const template = document.createElement("template");
            template.innerHTML = html.trim();
            const fresh = template.content.querySelector("#handover-note");
            if (!fresh) throw new Error("ZeitstromHandoverNoteTarget");
            swapRegion(current, fresh.outerHTML, "outerHTML");
            return true;
        }

        async function refreshChrome(component, signal, expectedGeneration, expectedValidator) {
            const config = {
                current_shift: [root.dataset.currentShiftUrl, "zeitstrom-current-shift-slot"],
                focus_box: [root.dataset.focusBoxUrl, "zeitstrom-focus-box-slot"],
                bans: [root.dataset.bansUrl, "zeitstrom-bans-slot"],
            }[component];
            if (!config) return;
            const html = await htmlFollowup(config[0], signal, expectedValidator);
            if (html === null || expectedGeneration !== generation || signal.aborted) return;
            const template = document.createElement("template");
            template.innerHTML = html.trim();
            const fresh = template.content.querySelector("#" + config[1]);
            const current = document.getElementById(config[1]);
            if (fresh && current) swapRegion(current, fresh.outerHTML, "outerHTML");
        }

        function refreshComponent(component, signal, expectedGeneration, expectedValidator) {
            if (!expectedValidator) return Promise.reject(snapshotMismatch());
            if (component === "feed") return refreshFeed(signal, expectedGeneration, expectedValidator);
            if (component === "tally_counts") {
                return refreshTallyCounts(signal, expectedGeneration, expectedValidator);
            }
            if (component === "handover_note") {
                return refreshHandoverNote(signal, expectedGeneration, expectedValidator);
            }
            return refreshChrome(component, signal, expectedGeneration, expectedValidator);
        }

        async function runCycle(currentCycle) {
            const headers = {
                Accept: "application/json",
                [PAGE_SCOPE_HEADER]: root.dataset.pageScope || "",
            };
            if (pageEtag) headers["If-None-Match"] = pageEtag;
            if (Object.keys(validators).length) headers[VALIDATORS_HEADER] = JSON.stringify(validators);
            const response = await fetchRequest(urlWithState(root.dataset.watchUrl), {
                method: "GET",
                credentials: "same-origin",
                cache: "no-store",
                redirect: "follow",
                signal: currentCycle.controller.signal,
                headers: headers,
            });
            if (currentCycle.generation !== generation || currentCycle.controller.signal.aborted) return;
            if (await authOrHtmlResponse(response)) return;
            if (response.status === 304) {
                pageEtag = response.headers.get("ETag") || pageEtag;
                failures = 0;
                return;
            }
            if (!response.ok) throw new Error("ZeitstromWatchHTTP" + response.status);
            const contentType = response.headers.get("Content-Type") || "";
            if (!contentType.includes("application/json")) {
                leaveSensitivePage("/login/");
                return;
            }
            const payload = await response.json();
            if (currentCycle.generation !== generation || currentCycle.controller.signal.aborted) return;
            const components = payload && Array.isArray(payload.components) ? payload.components : null;
            const changedValidators = payload && payload.validators && typeof payload.validators === "object"
                ? payload.validators
                : null;
            const validatorNames = changedValidators ? Object.keys(changedValidators) : [];
            const componentNames = components || [];
            const validProtocol =
                components !== null &&
                changedValidators !== null &&
                new Set(componentNames).size === componentNames.length &&
                componentNames.every(function (component) {
                    return WATCH_COMPONENTS.has(component) && typeof changedValidators[component] === "string";
                }) &&
                validatorNames.length === componentNames.length &&
                validatorNames.every(function (component) {
                    return componentNames.includes(component);
                });
            if (!validProtocol) {
                leaveSensitivePage(urlWithState(window.location.pathname));
                return;
            }
            if (components.includes("tally_topology")) {
                leaveSensitivePage(urlWithState(window.location.pathname));
                return;
            }
            let refreshResults;
            try {
                refreshResults = await Promise.all(
                    components.map(function (component) {
                        return refreshComponent(
                            component,
                            currentCycle.controller.signal,
                            currentCycle.generation,
                            changedValidators[component],
                        );
                    }),
                );
            } catch (error) {
                if (currentCycle.generation !== generation || stopped) throw error;
                generation += 1;
                currentCycle.controller.abort();
                components.forEach(function (component) {
                    delete validators[component];
                });
                pageEtag = "";
                if (error && error.name === "ZeitstromSnapshotMismatch") {
                    failures = 0;
                } else {
                    failures += 1;
                }
                return;
            }
            if (currentCycle.generation !== generation || currentCycle.controller.signal.aborted || stopped) return;
            let skippedComponent = false;
            Object.entries(changedValidators).forEach(function ([component, value]) {
                const index = components.indexOf(component);
                if (refreshResults[index] === false) {
                    skippedComponent = true;
                    return;
                }
                if (typeof value === "string") validators[component] = value;
            });
            pageEtag = skippedComponent ? "" : response.headers.get("ETag") || "";
            failures = 0;
        }

        function poll() {
            if (!canPoll() || pendingFilterTransition) return Promise.resolve();
            if (cycle && cycle.generation === generation) return cycle.promise;
            clearTimer();
            const currentCycle = {
                generation: generation,
                controller: new AbortController(),
                promise: null,
            };
            cycle = currentCycle;
            currentCycle.promise = runCycle(currentCycle)
                .catch(function (error) {
                    if (
                        currentCycle.generation === generation &&
                        !currentCycle.controller.signal.aborted &&
                        !stopped
                    ) {
                        failures += 1;
                    }
                    if (error && error.name === "AbortError") return;
                })
                .finally(function () {
                    if (cycle === currentCycle) {
                        cycle = null;
                        schedule();
                    }
                });
            return currentCycle.promise;
        }

        function pollImmediately() {
            if (!canPoll()) return;
            clearTimer();
            poll();
        }

        function transitionValidators(xhr) {
            if (!xhr || typeof xhr.getResponseHeader !== "function") return null;
            const etag = xhr.getResponseHeader("ETag");
            let advertised;
            try {
                advertised = JSON.parse(xhr.getResponseHeader(VALIDATORS_HEADER) || "");
            } catch (_error) {
                return null;
            }
            if (!advertised || typeof advertised !== "object" || Array.isArray(advertised)) return null;
            const names = Object.keys(advertised);
            if (
                names.length !== 2 ||
                !names.includes("feed") ||
                !names.includes("tally_topology") ||
                advertised.feed !== etag
            ) {
                return null;
            }
            const opaque = function (value) {
                return typeof value === "string" && value.length <= 256 && /^"[^"\r\n]+"$/.test(value);
            };
            if (!opaque(advertised.feed) || !opaque(advertised.tally_topology)) return null;
            return advertised;
        }

        function onHtmxConfigRequest(event) {
            if (!pendingFilterTransition || stopped) return;
            const detail = event && event.detail;
            const source = detail && (detail.elt || event.target);
            if (!source || !source.classList || !source.classList.contains("time-filter-tab")) return;
            if (!detail.headers || detail.headers[FILTER_TRANSITION_HEADER] !== "time_filter") return;
            if (stateSignature(detail.parameters) !== pendingFilterTransition.state) return;
            detail.headers[PREVIOUS_STATE_HEADER] = JSON.stringify(
                pendingFilterTransition.previousState,
            );
            detail.headers[VALIDATORS_HEADER] = JSON.stringify({
                tally_topology: pendingFilterTransition.previousTopology,
            });
            pendingFilterTransition.requestConfig = detail;
        }

        function rejectFilterTransition(preserveTopology) {
            abortedTransitionBaseline = preserveTopology && pendingFilterTransition
                ? {
                    previousState: pendingFilterTransition.previousState,
                    previousTopology: pendingFilterTransition.previousTopology,
                }
                : null;
            pendingFilterTransition = null;
            delete validators.feed;
            delete validators.tally_counts;
            if (!preserveTopology) delete validators.tally_topology;
            pageEtag = "";
            failures = 0;
            pollImmediately();
        }

        function onHtmxAfterRequest(event) {
            if (!pendingFilterTransition || stopped) return;
            const detail = event && event.detail;
            if (!detail || detail.requestConfig !== pendingFilterTransition.requestConfig) return;
            if (
                pendingFilterTransition.generation !== generation ||
                pendingFilterTransition.state !== stateParams().toString()
            ) {
                rejectFilterTransition(false);
                return;
            }
            const xhr = detail.xhr;
            const status = xhr ? xhr.status : 0;
            if (status === 401) {
                pendingFilterTransition = null;
                leaveSensitivePage("/login/");
                return;
            }
            if (status === 403 || status === 409) {
                pendingFilterTransition = null;
                leaveSensitivePage(window.location.href);
                return;
            }
            const advertised = detail.successful === true && status >= 200 && status < 300
                ? transitionValidators(xhr)
                : null;
            if (!advertised) {
                // hx-sync reports the replaced request as status 0 before it
                // configures the replacement. Keep only the page-rendered
                // topology proof long enough for that next request.
                rejectFilterTransition(status === 0);
                return;
            }
            pendingFilterTransition = null;
            abortedTransitionBaseline = null;
            validators.feed = advertised.feed;
            validators.tally_topology = advertised.tally_topology;
            pageEtag = "";
            failures = 0;
            pollImmediately();
        }

        function onVisibilityChange() {
            if (document.visibilityState === "visible") pollImmediately();
            else pause();
        }

        function onOnline() {
            pollImmediately();
        }

        function onOffline() {
            pause();
        }

        function onPageShow() {
            pageActive = true;
            pollImmediately();
        }

        function onPageHide() {
            pageActive = false;
            pause();
        }

        function onFilterChanged(event) {
            const detail = event && event.detail;
            if (detail && detail.name === "time_filter" && detail.deferWatch) return;
            clearTimer();
            abortCycle(true);
            const name = detail ? detail.name : "";
            delete validators.feed;
            if (!name || name === "date" || name === "time_filter" || name === "view") {
                delete validators.tally_counts;
                delete validators.tally_topology;
            }
            pageEtag = "";
            failures = 0;
            pendingFilterTransition = null;
            abortedTransitionBaseline = null;
            pollImmediately();
        }

        function onFilterTransition(event) {
            const baseline = abortedTransitionBaseline;
            abortedTransitionBaseline = null;
            const previousState = baseline
                ? baseline.previousState
                : stateObject(event && event.detail && event.detail.previousState);
            clearTimer();
            abortCycle(true);
            const previousTopology = baseline
                ? baseline.previousTopology
                : validators.tally_topology;
            delete validators.feed;
            delete validators.tally_counts;
            pageEtag = "";
            failures = 0;
            pendingFilterTransition = {
                generation: generation,
                state: stateParams().toString(),
                previousState: previousState,
                previousTopology: previousTopology,
                requestConfig: null,
            };
        }

        function onZeitstromChanged() {
            // The mutation's own #1911 HTMX path refreshes immediately. Do
            // not start a competing watch/follow-up cascade. Invalidate any
            // in-flight snapshot, retain the component validators for a
            // precise comparison, and let the next scheduled check converge.
            clearTimer();
            abortCycle(true);
            pageEtag = "";
            failures = 0;
            schedule();
        }

        function onQuickExitLeaving() {
            // ``stop`` bricht den laufenden fetch per AbortController ab und
            // raeumt den Timer; ``stopped`` verhindert jede Neuplanung.
            stop();
        }

        function start() {
            if (started || stopped) return;
            started = true;
            document.addEventListener("quick-exit:leaving", onQuickExitLeaving);
            document.addEventListener("visibilitychange", onVisibilityChange);
            window.addEventListener("online", onOnline);
            window.addEventListener("offline", onOffline);
            window.addEventListener("focus", pollImmediately);
            window.addEventListener("pageshow", onPageShow);
            window.addEventListener("pagehide", onPageHide);
            window.addEventListener("zeitstrom:filter-changed", onFilterChanged);
            window.addEventListener("zeitstrom:filter-transition", onFilterTransition);
            window.addEventListener("zeitstrom-changed", onZeitstromChanged);
            document.body.addEventListener("htmx:configRequest", onHtmxConfigRequest);
            document.body.addEventListener("htmx:afterRequest", onHtmxAfterRequest);
            pollImmediately();
        }

        return {
            start: start,
            stop: stop,
            pollNow: poll,
            checkNow: poll,
            isStopped: function () {
                return stopped;
            },
        };
    }

    window.createZeitstromLiveWatch = createZeitstromLiveWatch;
    if (!window.__ZEITSTROM_WATCH_TEST__) {
        const root = document.getElementById("zeitstrom-live-watch");
        if (root) {
            const watcher = createZeitstromLiveWatch(root);
            // Deterministic, PII-free manual/E2E hook. Validators and state
            // stay closure-private; callers can only request the same check
            // that focus/visibility would trigger.
            window.zeitstromWatch = Object.freeze({
                checkNow: function () {
                    return watcher.checkNow();
                },
            });
            watcher.start();
        }
    }
})();
