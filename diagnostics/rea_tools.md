# REA MCP tools/list: 229 tools
server: {"name":"rea","version":"6.8.0"}
protocolVersion: 2025-06-18
capabilities: {"completions":{},"prompts":{"listChanged":true},"tools":{"listChanged":true}}

## Tool names
address_name, address_to_file_offset, analyze_function, analyze_javascript_application, analyze_swift_types, analyze_web_bundle, annotate_native_function, batch_decompile, binary_overview, binary_session, build_call_path, build_reconstruction_obligation_ledger, capture_adb_screen, capture_browser_scenario, capture_electron_scenario, capture_native_ui_scenario, capture_process_scenario, capture_web_screenshot, close_binary, close_frida_session, close_gdb_session, close_rizin_debug_session, collect_adb_bugreport, comment, compare_application_versions, compare_artifacts, compare_bundles, compare_functions, compare_javascript_export_shapes, compare_managed_members, compare_process_captures, compare_source_to_bundle, compare_wasm_interfaces, compare_web_captures, compare_web_screenshots, correlate_static_and_runtime, current_address, current_document, current_procedure, cutter_command, decode_android_resources, decode_interface_builder, decode_signal_recording, decompile_all, decompile_jeb_item, demangle_swift, discover_webmcp_tools, evaluate_reconstruction_coverage, execute_rizin_command, export_environment_bundle, export_evidence_bundle, export_web_scripts, extract_artifact, extract_firmware, find_changed_behavior, find_xrefs_to_name, frida_session_status, gdb_console, gdb_session_status, get_call_graph, get_evidence_bundle, get_navigation_context, get_objc_classes, get_objc_protocols, goto_address, identify_flutter_build, import_evidence_bundle, import_managed_reconstruction, inline_comment, inspect_adb_client, inspect_adb_device, inspect_adb_display, inspect_adb_package, inspect_adb_window, inspect_address_context, inspect_analysis_settings, inspect_analysis_view, inspect_android_class, inspect_android_method, inspect_android_package, inspect_apk_signature, inspect_apktool_client, inspect_apple_ips, inspect_artifact, inspect_asset_catalog, inspect_binary_layout, inspect_byte_entropy, inspect_dart_aot, inspect_desktop_application, inspect_devicetree, inspect_electron_fuses, inspect_electron_page, inspect_elf_notes, inspect_erlang_beam, inspect_evm_interface, inspect_firmware_regions, inspect_go_binary, inspect_gpu_code_objects, inspect_il2cpp_metadata, inspect_jeb_client, inspect_kernel_module, inspect_keyed_archive, inspect_macho, inspect_managed_artifact, inspect_managed_members, inspect_managed_native_boundaries, inspect_model_artifact, inspect_native_api, inspect_native_data_type, inspect_native_dispatch_metadata, inspect_native_exports, inspect_native_instruction, inspect_native_load_image, inspect_packet_capture, inspect_packet_fields, inspect_pcap_capture, inspect_pcap_client, inspect_pe_certificates, inspect_pe_imports, inspect_pe_load_config, inspect_pe_resources, inspect_plist, inspect_python_pyc, inspect_recorded_crash, inspect_rust_binary, inspect_signal_decoders, inspect_signature, inspect_sqlite_database, inspect_wasm_artifact, inspect_web_event_listeners, inspect_web_network_capture, inspect_web_page, inspect_with_objdump, install_adb_package, instrument_with_frida, list_adb_devices, list_adb_directory, list_adb_features, list_adb_packages, list_adb_processes, list_adb_services, list_architectures, list_bookmarks, list_browser_targets, list_cutter_sessions, list_documents, list_electron_targets, list_frida_devices, list_frida_processes, list_javascript_runtime_targets, list_jeb_units, list_names, list_procedures, list_segments, list_strings, list_unknowns, load_frida_script, match_native_functions, next_address, observe_javascript_runtime, observe_native_calls, observe_native_ui, observe_web_execution, observe_web_session, open_binary, open_bundle_resources, open_jeb_project, prev_address, procedure_address, procedure_assembly, procedure_callees, procedure_callers, procedure_info, procedure_pseudo_code, procedure_references, project_android_application_graph, project_apple_application_graph, project_harmony_application_graph, project_managed_application_graph, pull_adb_file, pull_adb_package, push_adb_file, read_adb_logcat, read_adb_setting, read_bytes, read_function_instructions, reconcile_javascript_runtime, record_unknown, recover_javascript_sources, resolve_adb_packages, resolve_containing_procedure, resolve_native_call_targets, resume_frida_session, rizin_debug_command, rizin_debug_session_status, search_android_classes, search_bytes, search_procedures, search_strings, set_address_name, set_addresses_names, set_bookmark, set_comment, set_inline_comment, start_adb_activity, start_adb_app, start_frida_session, start_gdb_session, start_rizin_debug_session, stop_adb_app, trace_android_references, trace_application_feature, trace_call_path, trace_dylib_resolution, trace_feature, trace_javascript_semantics, trace_native_ui_action, trace_native_values, trace_web_module_imports, trace_web_source_location, uninstall_adb_package, unload_frida_script, unset_bookmark, update_analysis_settings, update_unknown, verify_managed_native_boundaries, verify_reconstruction, verify_unknown_resolution, xrefs

## address_name

Resolve the primary analyzed name at a code or data address. Headless providers require an explicit address; GUI providers may default to their current cursor. Null means the provider has no primary name at that address.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## address_to_file_offset

Map one provider-normalized virtual address to its original nonnegative file offset. Hopper verifies the original bytes against the selected executable digest and translates FAT image-relative offsets using the loaded header. Results preserve the provider coordinate, image base, and source path. Unmapped addresses fail explicitly; changed or unavailable sources return typed errors retaining native mapping facts as partial evidence.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## analyze_function

Build a dossier for one native function identified by symbol or provider-returned address. Returns identity with complete inclusive analyzed body ranges or explicit unknown, provider-specific pseudocode and assembly, comments, calls, references, referenced strings/names, local CFG blocks, and available native API boundary observations. Ghidra terminal-call assumptions that may cut the analyzed body are reported in limitations with available callee identities. Default detail summary returns a compact dossier (identity, pseudocode size, facet counts, value-flow availability) and retains the complete dossier Evidence in this session; page pseudocode, references, assembly and value-flow through normalized_result.parent_evidence_id with inspect_analysis_view. detail complete returns the full dossier inline.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## analyze_javascript_application

Reconstruct one local ASAR or extracted JavaScript application as an inline application graph without executing it. Returns recovered graph nodes, edges, semantic relations, integrity contradictions, limitations, and coverage. Select source_paths to acquire and analyze only exact inventoried file paths; the complete artifact inventory and identity remain intact, and unselected sources remain explicit unknowns. Dependencies are not implicitly analyzed. Select detail summary to retain the complete analysis Evidence in this session and receive only its summary view, then inspect selected modules or pages with inspect_analysis_view. Select a javascript-routes view with summary detail to query deduplicated static patterns, source-linked occurrences, source status or interpretation gaps with filters, fields and paging. Static routes do not establish reachability or the full application route set. Summary detail reduces delivery, whereas source_paths bounds source analysis. Integrity mismatches fail by default; record-and-continue retains observed bytes as untrusted and marks graph coverage partial. Timeout recovery: returned provider_timeout uses the per-source analysis_timeout_ms budget. A host-only 'timed out awaiting tools/call' needs a longer client tool deadline, not a larger analysis_timeout_ms. Never repeat the unchanged call to test whether that timeout persists; report the host setting if configuration is not editable (Codex: mcp_servers.<server>.tool_timeout_sec, seconds).

```json
{
    "type":  "object",
    "properties":  {
                       "input_path":  {
                                          "type":  "string",
                                          "description":  "Absolute path to the JavaScript application on the machine running REA."
                                      },
                       "format":  {
                                      "type":  "string",
                                      "description":  "Declared input artifact format."
                                  },
                       "integrity_policy":  {
                                                "type":  "string"
                                            },
                       "source_paths":  {
                                            "type":  "array",
                                            "items":  {
                                                          "type":  [
                                                                       "null",
                                                                       "boolean",
                                                                       "object",
                                                                       "array",
                                                                       "number",
                                                                       "string"
                                                                   ],
                                                          "items":  {

                                                                    }
                                                      },
                                            "description":  "Exact relevant file paths from the artifact inventory, relative to the application root, analyzed in first-request order before unselected scopes; no globs or implicit dependencies. Omit to analyze all sources."
                                        },
                       "max_semantic_nodes":  {
                                                  "type":  "integer",
                                                  "description":  "Maximum retained semantic nodes across the application; defaults to 100000. Worker heap and parent result admission remain enforced."
                                              },
                       "max_semantic_nodes_per_file":  {
                                                           "type":  "integer",
                                                           "description":  "Maximum retained semantic nodes per source file; defaults to 20000, additionally bounded by max_semantic_nodes and memory/transfer limits."
                                                       },
                       "max_semantic_unknowns":  {
                                                     "type":  "integer",
                                                     "description":  "Maximum retained semantic unknown records across the application; defaults to 100000. Omitted emission occurrences are counted by family and reason."
                                                 },
                       "max_semantic_unknowns_per_file":  {
                                                              "type":  "integer",
                                                              "description":  "Maximum retained semantic unknown records per source file; defaults to 20000, additionally bounded by max_semantic_unknowns and memory/transfer limits."
                                                          },
                       "max_heap_mb":  {
                                           "type":  "integer",
                                           "description":  "Maximum V8 old-space heap in MiB for the isolated JavaScript source analyzer; defaults to 1024. This does not change the CLI/MCP process heap. When REA runs on Bun, which ignores V8 heap flags, Windows instead limits the analyzer\u0027s committed memory to this budget plus a 512 MiB runtime reserve; other Bun hosts enforce no limit."
                                       },
                       "analysis_timeout_ms":  {
                                                   "type":  "integer",
                                                   "description":  "Maximum startup, analysis and transfer time per JavaScript source, in milliseconds; defaults to 300000. On timeout, return collected partial Evidence and stop the analysis."
                                               },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "input_path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "minProperties":  1,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## analyze_swift_types

Categorize analyzed Swift procedure names using their manglings or same-address Swift symbol aliases. Decodes legacy and literal modern nominal contexts into classes, structs, enums, protocols, and extensions. Preserves provider names and supporting mangled_names; unresolved categories appear in other and in unclassified, which remains inline even under category filters. Returns deduplicated procedure-name counts, not unique source types, plus identification limitations. Optionally select one category and/or apply a case-sensitive literal filter to provider names or supporting manglings; for example, use {category: "structs", pattern: "Pair"}.

```json
{
    "type":  "object",
    "properties":  {
                       "category":  {
                                        "type":  "string",
                                        "enum":  [
                                                     "classes",
                                                     "structs",
                                                     "enums",
                                                     "protocols",
                                                     "extensions",
                                                     "other"
                                                 ]
                                    },
                       "pattern":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## analyze_web_bundle

Capture JavaScript source from one selected CDP page and statically derive a chunk graph, route and endpoint candidates, vendor fingerprints, page-declared WebMCP metadata, and optionally fetch source maps from the selected target origin or explicitly requested origins. JavaScript is parsed but never executed. Select detail summary and an explicit javascript-routes view for filtered route patterns, occurrences, sources, unknowns or summary counts; complete Evidence remains retained for later views. Static candidates do not establish reachability or a complete application route denominator.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "description":  "Literal loopback CDP endpoint: http://127.0.0.1:PORT, or ws://127.0.0.1:PORT/devtools/browser/ID."
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  [
                                                                          "null",
                                                                          "boolean",
                                                                          "object",
                                                                          "array",
                                                                          "number",
                                                                          "string"
                                                                      ],
                                                             "items":  {

                                                                       }
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "description":  "Exact CDP target identifier selected from the endpoint\u0027s target listing."
                                     },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "description":  "CDP WebSocket handshake and per-command response deadline in milliseconds (default 20000). Omitted means 20000 and applies on the next call. deadlineExpired for a handshake or command is not the caller observation window, not the 10000 ms CDP discovery response deadline, and not the 5000 ms cleanup grace. It also bounds passive script-source collection and the inspection/source-join phase that share this deadline. Does not raise the fixed protocol, retained-event, or source byte budgets. CDP discovery and cleanup grace have no runtime override."
                                              },
                       "observation_ms":  {
                                              "type":  "integer"
                                          },
                       "include_accessibility_text":  {
                                                          "type":  "boolean",
                                                          "description":  "Whether to include accessibility text in the result."
                                                      },
                       "include_console_text":  {
                                                    "type":  "boolean",
                                                    "description":  "Whether to include console text in the result."
                                                },
                       "include_json_body_shapes":  {
                                                        "type":  "boolean",
                                                        "description":  "Whether to include json body shapes in the result."
                                                    },
                       "include_websocket_shapes":  {
                                                        "type":  "boolean",
                                                        "description":  "Whether to include websocket shapes in the result."
                                                    },
                       "include_storage_keys":  {
                                                    "type":  "boolean",
                                                    "description":  "Whether to include storage keys in the result."
                                                },
                       "include_storage_fingerprints":  {
                                                            "type":  "boolean",
                                                            "description":  "Whether to include storage fingerprints in the result."
                                                        },
                       "include_script_sources":  {
                                                      "type":  "boolean",
                                                      "description":  "Whether to include script sources in the result."
                                                  },
                       "fetch_source_maps":  {
                                                 "type":  "boolean"
                                             },
                       "max_heap_mb":  {
                                           "type":  "integer",
                                           "description":  "Heap phase: maximum V8 old-space for the isolated inert-script analyzer, in MiB (default 1024, range 128..16384). Omitted means 1024 and applies on the next call. This is not a total process RSS limit, not analysis_timeout_ms, and not total_analysis_timeout_ms. Bun ignores V8 flags; Windows Bun uses an owned process commit limit plus a 512 MiB runtime reserve, while other Bun hosts have no enforced memory limit. Raising max_heap_mb does not raise the fixed worker protocol or result byte budgets."
                                       },
                       "analysis_timeout_ms":  {
                                                   "type":  "integer",
                                                   "description":  "Per-script deadline phase: maximum startup, analysis and result transfer time for one captured script, in milliseconds (default 30000, range 1..2147483647). Omitted means 30000. deadlineExpired for one script is not total_analysis_timeout_ms. A per-script value above the effective total is rejected. Timed-out scripts are explicit unknowns and other scripts may still be analyzed. Applies on the next call."
                                               },
                       "total_analysis_timeout_ms":  {
                                                         "type":  "integer",
                                                         "description":  "Total analysis deadline phase: maximum time for all captured-script analysis, in milliseconds (default 300000, range 1..2147483647). Omitted means 300000. This is not the per-script analysis_timeout_ms, not capture, and not source-map fetch. A per-script timeout above this total is rejected. Unstarted scripts remain explicit unknowns. Applies on the next call."
                                                     },
                       "source_map_fetch_timeout_ms":  {
                                                           "type":  "integer",
                                                           "description":  "Source-map fetch deadline in milliseconds (default 30000). Omitted means 30000 and applies on the next fetch. A fetch overrun is an item limitation (fetch_failed), not a thrown analysis timeout. Does not raise the fixed 64 MiB response budget or the source-map decode deadline. The response budget has no runtime override."
                                                       },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "minProperties":  2,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## annotate_native_function

Atomically update one function name and/or entry comments, read them back, and return a refreshed complete function dossier. Ghidra changes its ephemeral session analysis database only; original executable bytes remain unchanged. Empty comment text clears that comment. Edits remain available to subsequent MCP calls until close; CLI returns the updated dossier before discarding its session. Invalid edits roll back the entire operation. Windows P0 and providers without this capability are unsupported.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "name":  {
                                    "type":  "string",
                                    "minLength":  1
                                },
                       "comment":  {
                                       "type":  "string"
                                   },
                       "inline_comment":  {
                                              "type":  "string"
                                          }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":true}

## batch_decompile

Decompile each explicit procedure symbol or address, with one provider read active per batch. Preserves each input selector in address and reports the observed canonical entry and entry label in procedure; identity or label failures remain explicit without discarding usable pseudocode. Returns ordered per-item success or error results and aggregate counts. The complete reply retains every pseudocode body; use selected small batches to bound retained output. A per-function limit is not a batch deadline; allow for every item or split calls.

```json
{
    "type":  "object",
    "properties":  {
                       "addresses":  {
                                         "type":  "array",
                                         "items":  {
                                                       "type":  "string"
                                                   }
                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## binary_overview

Return the bound document and exhaustive procedure/string counts, plus every segment and segment_count when the provider supports segment enumeration. If segment fields are omitted, segment coverage is unavailable rather than empty. When every observed segment agrees on an analysis image base, return it as image_base so callers can relate provider addresses to their loader coordinates. Use search_procedures, list_procedures, or analyze_function directly when you already know which procedure to inspect.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## binary_session

Report the active binary target or resource-only app root, provider and capability availability when applicable, client-feature, analysis, and server-identity status without starting analysis.

```json
{
    "type":  "object",
    "properties":  {
                       "expected_package_version":  {
                                                        "type":  "string",
                                                        "minLength":  1
                                                    },
                       "expected_catalog_digest":  {
                                                       "type":  "string",
                                                       "pattern":  "^[a-f0-9]{64}$"
                                                   },
                       "expected_server_path":  {
                                                    "type":  "string",
                                                    "minLength":  1,
                                                    "allOf":  [
                                                                  {
                                                                      "pattern":  "^[^\\x00]*$"
                                                                  },
                                                                  {
                                                                      "pattern":  "^\\/"
                                                                  }
                                                              ]
                                                }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## build_call_path

Build every shortest direct-callee path inline from complete analyze_function Evidence records using exact canonical addresses. Missing dossiers and provider mixing remain unknown; every node and edge cites source Evidence.

```json
{
    "type":  "object",
    "properties":  {
                       "functions":  {
                                         "type":  "array",
                                         "items":  {
                                                       "type":  [
                                                                    "null",
                                                                    "boolean",
                                                                    "object",
                                                                    "array",
                                                                    "number",
                                                                    "string"
                                                                ],
                                                       "items":  {

                                                                 }
                                                   }
                                     },
                       "start":  {
                                     "type":  "object"
                                 },
                       "goal":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "functions",
                     "start",
                     "goal"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## build_reconstruction_obligation_ledger

Generate the complete deterministic ReconstructionObligationLedger from an authenticated Evidence bundle, reviewed obligations, and an explicit reconstruction manifest. Static candidates remain candidates; duplicate ownership, missing original or reconstruction cases, missing parser/type, weak verifier authority, unenumerated claims, contradictions, dependencies, and residual unknowns fail closed. The inline result carries the full-ledger closure digest and typed per-obligation diagnostics.

```json
{
    "type":  "object",
    "properties":  {
                       "evidence_bundle":  {
                                               "type":  "object"
                                           },
                       "reviewed_obligations":  {
                                                    "type":  "array",
                                                    "items":  {
                                                                  "type":  [
                                                                               "null",
                                                                               "boolean",
                                                                               "object",
                                                                               "array",
                                                                               "number",
                                                                               "string"
                                                                           ],
                                                                  "items":  {

                                                                            }
                                                              }
                                                },
                       "manifest":  {
                                        "type":  "object"
                                    }
                   },
    "required":  [
                     "evidence_bundle",
                     "reviewed_obligations",
                     "manifest"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## capture_adb_screen

Capture one frame of the device screen as a PNG (`screencap -p` through exec-out, no device-side file) into a caller-selected output directory, digesting it and reporting its dimensions. No recording or interaction.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "output_directory":  {
                                                "type":  "string",
                                                "minLength":  1,
                                                "pattern":  "^[^\\x00]*$"
                                            }
                   },
    "required":  [
                     "serial",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## capture_browser_scenario

Run a controlled browser scenario when passive observation cannot exercise the application. Launch the selected executable or connect to a loopback CDP target, provide a start URL and actions, and select storage or artifacts when needed. A request action sends one HTTP request to the start origin through the page's own fetch, with header values from secrets, page cookies or local storage; write methods change application state. Environment, empty storage, a URL-only final capture, and measured event-retention budgets are defaults. capture.network selects request_body, response_body, and header_values independently; capture.limits lets callers raise or lower the event count, retained-allocation estimates, network transaction count, and network content budgets. Selected content is returned inline with transaction and event references, byte digests, and explicit unavailable states. Budget omissions identify affected event kinds and network transactions. Credentials and declared secrets are redacted. Agents can run their own local fixture servers to control application responses. Actions run until completion or cancellation unless timeout_ms is set. Results are returned inline.

```json
{
    "type":  "object",
    "properties":  {
                       "browser":  {
                                       "type":  "object",
                                       "description":  "Required launch/connect selection. Launch uses the selected executable and an owned temporary profile; connect uses one loopback CDP endpoint and target."
                                   },
                       "start_url":  {
                                         "type":  "object",
                                         "description":  "Required initial HTTP(S) URL. Query values must be declared separately as public literals or secret references."
                                     },
                       "environment":  {
                                           "type":  "object",
                                           "description":  "Optional deterministic browser settings. Defaults to 1280x720, en-US, UTC, light mode, reduced motion, and blocked service workers."
                                       },
                       "actions":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 },
                                       "description":  "Required sequence of explicit browser actions."
                                   },
                       "storage":  {
                                       "type":  "object",
                                       "description":  "Optional initial cookies, local storage, or session storage. Defaults to empty; every supplied value and origin must be declared and within the allowed origins."
                                   },
                       "secrets":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 },
                                       "description":  "Optional environment-variable references for values used in actions, URLs, storage, or replay, and for redacting matching observed text. Secret values are never supplied inline."
                                   },
                       "capture":  {
                                       "type":  "object",
                                       "description":  "Optional retained artifacts and event families. Defaults to only a final sanitized URL; request screenshots, DOM, accessibility, history, storage, and event capture explicitly."
                                   },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "description":  "CDP WebSocket handshake and per-command response deadline in milliseconds (default 20000). Omitted means 20000 and applies on the next call. deadlineExpired for a handshake or command is not the caller observation window, not the 10000 ms CDP discovery response deadline, and not the 5000 ms cleanup grace. It also bounds passive script-source collection and the inspection/source-join phase that share this deadline. Does not raise the fixed protocol, retained-event, or source byte budgets. CDP discovery and cleanup grace have no runtime override."
                                              }
                   },
    "required":  [
                     "browser",
                     "start_url",
                     "actions"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## capture_electron_scenario

Use this for a provider-owned Electron run when passive CDP or Inspector observation cannot exercise application behavior. REA owns startup and teardown, accepts caller-defined click/wait actions plus window-targeted renderer reload/crash and synthetic open-url/second-instance delivery, and returns correlated window/WebContents/process/preload/session/navigation/shell/IPC evidence without retaining payload values. Actions wait for completion or request cancellation; a missing click target can keep the request pending. Results identify observed and unavailable event families, coverage status, action targets, and truncation. Cleanup failures retain collected observations with explicit cleanup uncertainty. External shell, navigation, permission, download, popup, updater, and OS-integration effects are blocked and recorded. Use passive Electron tools for observation-only work.

```json
{
    "type":  "object",
    "properties":  {
                       "executable_path":  {
                                               "type":  "string",
                                               "description":  "Absolute local filesystem path for the Electron executable; relative paths are rejected."
                                           },
                       "application_path":  {
                                                "type":  "string",
                                                "description":  "Absolute local filesystem path for the Electron application; relative paths are rejected."
                                            },
                       "application_root":  {
                                                "type":  "string",
                                                "description":  "Absolute local filesystem root for application-relative paths; omit to derive it from the application path. Relative paths are rejected."
                                            },
                       "args":  {
                                    "type":  "array",
                                    "items":  {
                                                  "type":  [
                                                               "null",
                                                               "boolean",
                                                               "object",
                                                               "array",
                                                               "number",
                                                               "string"
                                                           ],
                                                  "items":  {

                                                            }
                                              }
                                },
                       "actions":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 }
                                   }
                   },
    "required":  [
                     "executable_path",
                     "application_path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## capture_native_ui_scenario

Run selected-element press, increment/decrement scroll, AXValue text entry and bounded wait steps in one exact native app window. Returns ordered before/after captures and action/capture failures. Actions can change app data, network activity and persistent state; application state is left as-is and restoration is not attempted. native_ui_deadline_ms is the scenario deadline (omitted means 180000 ms, range 1..2147483647, next call). It does not raise the fixed 67108864-byte output allowance or the 180000 ms wait-step cap.

```json
{
    "type":  "object",
    "properties":  {
                       "pid":  {
                                   "type":  "integer"
                               },
                       "window_id":  {
                                         "type":  "integer"
                                     },
                       "screenshot":  {
                                          "type":  "boolean"
                                      },
                       "accessibility":  {
                                             "type":  "boolean"
                                         },
                       "max_nodes":  {
                                         "type":  "integer",
                                         "description":  "Maximum accessibility nodes per capture; increase for large windows."
                                     },
                       "native_ui_deadline_ms":  {
                                                     "type":  "integer",
                                                     "description":  "Deadline in milliseconds for this UI observation or scenario, including helper preparation and captures. Omitted means 180000. Integer from 1 to 2147483647. Applies on the next call. Does not raise the fixed 67108864-byte scenario output allowance or the 180000 ms cap on one wait step. A partial observation may already be retained when this deadline expires."
                                                 },
                       "steps":  {
                                     "type":  "array",
                                     "items":  {
                                                   "type":  [
                                                                "null",
                                                                "boolean",
                                                                "object",
                                                                "array",
                                                                "number",
                                                                "string"
                                                            ],
                                                   "items":  {

                                                             }
                                               }
                                 }
                   },
    "required":  [
                     "pid",
                     "window_id",
                     "steps"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## capture_process_scenario

Run one caller-selected command under a PTY and return process capture Evidence with residual unknowns. The renderer admits at most 1,000,000 combined terminal cells, computed as columns × (rows + scrollback); every resize uses the selected scrollback and must fit the same budget. Supply a smaller terminal shape when that product is too large; dimensions are never clamped. This release has no runtime override for that cell budget. A command name resolves through the inherited PATH; the working directory defaults to the caller's current directory; host environment variables are inherited with scenario overrides. Environment keys cannot contain '=' or NUL, and REA_PROCESS_RUN_ID is reserved for process ownership. Filesystem snapshots are opt-in through filesystem_observation_paths. A positive finalization_ms makes a timeout or idle deadline send SIGTERM to the captured root through its start identity, keep observing, and send SIGKILL once after the interval; exit.finalization records each attempt with its delivery result and the observed exit, and cancellation still ends the run at once. The target runs with the current user's permissions; this is not a security sandbox.

```json
{
    "type":  "object",
    "properties":  {
                       "executable":  {
                                          "type":  "string",
                                          "minLength":  1,
                                          "pattern":  "^[^\\x00]*$"
                                      },
                       "arguments":  {
                                         "type":  "array",
                                         "items":  {
                                                       "type":  "string",
                                                       "pattern":  "^[^\\x00]*$"
                                                   }
                                     },
                       "working_directory":  {
                                                 "type":  "string",
                                                 "pattern":  "^[^\\x00]*$"
                                             },
                       "environment":  {
                                           "type":  "object",
                                           "propertyNames":  {
                                                                 "type":  "string",
                                                                 "minLength":  1,
                                                                 "allOf":  [
                                                                               {
                                                                                   "pattern":  "^(?:[^=\\x00R][^=\\x00]*|R(?:[^=\\x00E][^=\\x00]*|E(?:[^=\\x00A][^=\\x00]*|A(?:[^=\\x00_][^=\\x00]*|_(?:[^=\\x00P][^=\\x00]*|P(?:[^=\\x00R][^=\\x00]*|R(?:[^=\\x00O][^=\\x00]*|O(?:[^=\\x00C][^=\\x00]*|C(?:[^=\\x00E][^=\\x00]*|E(?:[^=\\x00S][^=\\x00]*|S(?:[^=\\x00S][^=\\x00]*|S(?:[^=\\x00_][^=\\x00]*|_(?:[^=\\x00R][^=\\x00]*|R(?:[^=\\x00U][^=\\x00]*|U(?:[^=\\x00N][^=\\x00]*|N(?:[^=\\x00_][^=\\x00]*|_(?:[^=\\x00I][^=\\x00]*|I(?:[^=\\x00D][^=\\x00]*|D[^=\\x00]+)?)?)?)?)?)?)?)?)?)?)?)?)?)?)?)?)?)$"
                                                                               },
                                                                               {
                                                                                   "pattern":  "^(?:[^=\\x00_][^=\\x00]*|_(?:[^=\\x00_][^=\\x00]*|_(?:[^=\\x00p][^=\\x00]*|p(?:[^=\\x00r][^=\\x00]*|r(?:[^=\\x00o][^=\\x00]*|o(?:[^=\\x00t][^=\\x00]*|t(?:[^=\\x00o][^=\\x00]*|o(?:[^=\\x00_][^=\\x00]*|_(?:[^=\\x00_][^=\\x00]*|_[^=\\x00]+)?)?)?)?)?)?)?)?)$"
                                                                               }
                                                                           ]
                                                             },
                                           "additionalProperties":  {
                                                                        "type":  "string",
                                                                        "pattern":  "^[^\\x00]*$"
                                                                    }
                                       },
                       "filesystem_observation_paths":  {
                                                            "type":  "array",
                                                            "items":  {
                                                                          "type":  "string",
                                                                          "minLength":  1,
                                                                          "pattern":  "^[^\\x00]*$"
                                                                      }
                                                        },
                       "terminal":  {
                                        "type":  "object",
                                        "properties":  {
                                                           "columns":  {
                                                                           "type":  "integer",
                                                                           "minimum":  1,
                                                                           "maximum":  65535
                                                                       },
                                                           "rows":  {
                                                                        "type":  "integer",
                                                                        "minimum":  1,
                                                                        "maximum":  65535
                                                                    },
                                                           "scrollback":  {
                                                                              "type":  "integer",
                                                                              "minimum":  0,
                                                                              "maximum":  9007199254740991
                                                                          }
                                                       },
                                        "additionalProperties":  false
                                    },
                       "events":  {
                                      "type":  "array",
                                      "items":  {
                                                    "oneOf":  [
                                                                  {
                                                                      "type":  "object",
                                                                      "properties":  {
                                                                                         "at_ms":  {
                                                                                                       "type":  "integer",
                                                                                                       "minimum":  0,
                                                                                                       "maximum":  9007199254740991
                                                                                                   },
                                                                                         "type":  {
                                                                                                      "type":  "string",
                                                                                                      "const":  "input"
                                                                                                  },
                                                                                         "data":  {
                                                                                                      "type":  "string"
                                                                                                  },
                                                                                         "sensitive":  {
                                                                                                           "type":  "boolean"
                                                                                                       }
                                                                                     },
                                                                      "required":  [
                                                                                       "at_ms",
                                                                                       "type",
                                                                                       "data"
                                                                                   ],
                                                                      "additionalProperties":  false
                                                                  },
                                                                  {
                                                                      "type":  "object",
                                                                      "properties":  {
                                                                                         "at_ms":  {
                                                                                                       "type":  "integer",
                                                                                                       "minimum":  0,
                                                                                                       "maximum":  9007199254740991
                                                                                                   },
                                                                                         "type":  {
                                                                                                      "type":  "string",
                                                                                                      "const":  "resize"
                                                                                                  },
                                                                                         "columns":  {
                                                                                                         "type":  "integer",
                                                                                                         "minimum":  1,
                                                                                                         "maximum":  65535
                                                                                                     },
                                                                                         "rows":  {
                                                                                                      "type":  "integer",
                                                                                                      "minimum":  1,
                                                                                                      "maximum":  65535
                                                                                                  }
                                                                                     },
                                                                      "required":  [
                                                                                       "at_ms",
                                                                                       "type",
                                                                                       "columns",
                                                                                       "rows"
                                                                                   ],
                                                                      "additionalProperties":  false
                                                                  },
                                                                  {
                                                                      "type":  "object",
                                                                      "properties":  {
                                                                                         "at_ms":  {
                                                                                                       "type":  "integer",
                                                                                                       "minimum":  0,
                                                                                                       "maximum":  9007199254740991
                                                                                                   },
                                                                                         "type":  {
                                                                                                      "type":  "string",
                                                                                                      "const":  "signal"
                                                                                                  },
                                                                                         "signal":  {
                                                                                                        "type":  "string",
                                                                                                        "enum":  [
                                                                                                                     "SIGINT",
                                                                                                                     "SIGTERM",
                                                                                                                     "SIGKILL"
                                                                                                                 ]
                                                                                                    }
                                                                                     },
                                                                      "required":  [
                                                                                       "at_ms",
                                                                                       "type",
                                                                                       "signal"
                                                                                   ],
                                                                      "additionalProperties":  false
                                                                  }
                                                              ],
                                                    "type":  "object"
                                                }
                                  },
                       "timeout_ms":  {
                                          "type":  "integer",
                                          "exclusiveMinimum":  0,
                                          "maximum":  9007199254740991
                                      },
                       "idle_timeout_ms":  {
                                               "type":  "integer",
                                               "exclusiveMinimum":  0,
                                               "maximum":  9007199254740991
                                           },
                       "settle_ms":  {
                                         "type":  "integer",
                                         "minimum":  0,
                                         "maximum":  9007199254740991
                                     },
                       "finalization_ms":  {
                                               "type":  "integer",
                                               "minimum":  0,
                                               "maximum":  9007199254740991
                                           },
                       "limits":  {
                                      "type":  "object",
                                      "properties":  {
                                                         "output_bytes":  {
                                                                              "type":  "integer",
                                                                              "exclusiveMinimum":  0,
                                                                              "maximum":  9007199254740991
                                                                          },
                                                         "files":  {
                                                                       "type":  "integer",
                                                                       "exclusiveMinimum":  0,
                                                                       "maximum":  9007199254740991
                                                                   },
                                                         "file_bytes":  {
                                                                            "type":  "integer",
                                                                            "exclusiveMinimum":  0,
                                                                            "maximum":  9007199254740991
                                                                        },
                                                         "processes":  {
                                                                           "type":  "integer",
                                                                           "exclusiveMinimum":  0,
                                                                           "maximum":  9007199254740991
                                                                       },
                                                         "filesystem_depth":  {
                                                                                  "type":  "integer",
                                                                                  "minimum":  0,
                                                                                  "maximum":  9007199254740991
                                                                              }
                                                     },
                                      "additionalProperties":  false
                                  },
                       "normalization":  {
                                             "type":  "object",
                                             "properties":  {
                                                                "paths":  {
                                                                              "type":  "boolean"
                                                                          },
                                                                "pids":  {
                                                                             "type":  "boolean"
                                                                         },
                                                                "ports":  {
                                                                              "type":  "boolean"
                                                                          },
                                                                "time_bucket_ms":  {
                                                                                       "type":  "integer",
                                                                                       "exclusiveMinimum":  0,
                                                                                       "maximum":  9007199254740991
                                                                                   },
                                                                "patterns":  {
                                                                                 "type":  "array",
                                                                                 "items":  {
                                                                                               "type":  "object",
                                                                                               "properties":  {
                                                                                                                  "pattern":  {
                                                                                                                                  "type":  "string"
                                                                                                                              },
                                                                                                                  "replacement":  {
                                                                                                                                      "type":  "string"
                                                                                                                                  }
                                                                                                              },
                                                                                               "required":  [
                                                                                                                "pattern",
                                                                                                                "replacement"
                                                                                                            ],
                                                                                               "additionalProperties":  false
                                                                                           }
                                                                             }
                                                            },
                                             "additionalProperties":  false
                                         }
                   },
    "required":  [
                     "executable"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## capture_web_screenshot

Capture the current visible viewport of one configured page as an inline, content-addressed PNG artifact. The operation never scrolls, navigates, or evaluates page JavaScript.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## close_binary

When the investigation is finished, drain earlier provider requests, optionally write a provider-neutral analysis snapshot atomically to the caller-supplied path, then close the active analysis session and every provider resource started for it. Resource-only app sessions have no binary snapshot binding, so close them without snapshot_path. Later provider requests wait until this lifecycle operation finishes. Existing files require explicit overwrite; a failed save leaves the session open so cached analysis is not lost. Metadata edits prevent immutable snapshot saves until the session is recreated; export_evidence_bundle preserves mutable observations before closing without a snapshot.

```json
{
    "type":  "object",
    "properties":  {
                       "snapshot_path":  {
                                             "type":  "string",
                                             "minLength":  1,
                                             "allOf":  [
                                                           {
                                                               "pattern":  "^[^\\x00]*$"
                                                           },
                                                           {
                                                               "pattern":  "^\\/"
                                                           }
                                                       ]
                                         },
                       "overwrite":  {
                                         "type":  "boolean"
                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":false}

## close_frida_session

Unload session scripts and attempt to detach REA's Frida session. Then resume a still-paused spawned process if script cleanup or session detach confirms instrumentation was released. Cleanup failures are reported. REA never kills the target; its final state is reported as unknown.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## close_gdb_session

Close the owned GDB process after verifying the inferior state. When one live thread group has a verified durable output TTY, REA automatically detaches it, which may resume target execution; otherwise close reports that a disposition is required. REA never kills attached or remote targets as part of debugger cleanup. In a shared MCP server, this admitted close retires any active binary-analysis session; after target effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## close_rizin_debug_session

Close the REA-owned Rizin debugger only after verifying no inferior remains or detaching one with a verified caller-selected TTY output sink. A detached target may continue running and write to that TTY; target state is otherwise reported as unknown. REA never signals an inferior based on PID or path. In a shared MCP server, this admitted close retires any active binary-analysis session; after target effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":true}

## collect_adb_bugreport

Collect one device-generated bugreport archive (`adb bugreport`) into a caller-selected output directory, digesting the zip. The archive contains the device's own dumpstate, dumpsys, and logcat report; collection can take minutes. Read-only with respect to configuration.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "output_directory":  {
                                                "type":  "string",
                                                "minLength":  1,
                                                "pattern":  "^[^\\x00]*$"
                                            }
                   },
    "required":  [
                     "serial",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## comment

Read the regular analysis comment at an address, defaulting to the current cursor. This is read-only and returns null when no comment exists; use set_comment to persist a finding.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_application_versions

Compare two authenticated JavaScript Application Graph versions supplied as inline Evidence or exact same-session retained references. Uses unique-only exact digest, module source digest, source-map identity, structural fingerprint, and non-module semantic-key tiers. Reports added, removed, changed, ambiguous, and unknown entities plus the complete matching changed_from graph without fuzzy or module-ordinal pairing.

```json
{
    "type":  "object",
    "properties":  {
                       "left_native_observations":  {
                                                        "type":  "array",
                                                        "items":  {
                                                                      "type":  [
                                                                                   "null",
                                                                                   "boolean",
                                                                                   "object",
                                                                                   "array",
                                                                                   "number",
                                                                                   "string"
                                                                               ],
                                                                      "items":  {

                                                                                }
                                                                  }
                                                    },
                       "right_native_observations":  {
                                                         "type":  "array",
                                                         "items":  {
                                                                       "type":  [
                                                                                    "null",
                                                                                    "boolean",
                                                                                    "object",
                                                                                    "array",
                                                                                    "number",
                                                                                    "string"
                                                                                ],
                                                                       "items":  {

                                                                                 }
                                                                   }
                                                     },
                       "left":  {
                                    "type":  "object",
                                    "description":  "Left-hand input used for comparison or differential execution."
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Right-hand input used for comparison or differential execution."
                                 }
                   },
    "required":  [
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_artifacts

Compare complete artifact inventories by logical occurrence path, content identity, metadata, and graph relations. Pass the inventory Evidence nested in each inspect_artifact result as left and right, or pass its inspect_analysis_view summary while the complete parent Evidence remains retained in this session. Summary facts are not treated as a complete inventory; comparison resolves the exact retained parent. Every delta cites both complete inputs, and gaps yield truncated or unknown, never equivalence. Returns every change inline.

```json
{
    "type":  "object",
    "properties":  {
                       "left":  {
                                    "type":  "object",
                                    "description":  "Left-hand input used for comparison or differential execution."
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Right-hand input used for comparison or differential execution."
                                 }
                   },
    "required":  [
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_bundles

Compare two canonical Evidence bundles by exact record membership, explicit one-to-one observation pairs, and complete residual-unknown revision histories. Supply absolute local filesystem paths; relative paths are rejected. Missing bundle members describe omission only, never behavioral equivalence; output is digest-anchored and returns every change inline.

```json
{
    "type":  "object",
    "properties":  {
                       "left_bundle_path":  {
                                                "type":  "string",
                                                "description":  "Absolute local filesystem path to the left Evidence bundle."
                                            },
                       "right_bundle_path":  {
                                                 "type":  "string",
                                                 "description":  "Absolute local filesystem path to the right Evidence bundle."
                                             },
                       "record_pairs":  {
                                            "type":  "array",
                                            "items":  {
                                                          "type":  [
                                                                       "null",
                                                                       "boolean",
                                                                       "object",
                                                                       "array",
                                                                       "number",
                                                                       "string"
                                                                   ],
                                                          "items":  {

                                                                    }
                                                      }
                                        }
                   },
    "required":  [
                     "left_bundle_path",
                     "right_bundle_path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_functions

Compare two explicit analyze_function Evidence records or exact retained session references across identity, exact provider text, calls, references, strings, and address-normalized CFG topology. Missing or provider-incompatible facets remain truncated or unknown; every conclusion cites both Evidence sets.

```json
{
    "type":  "object",
    "properties":  {
                       "left":  {
                                    "type":  "object",
                                    "description":  "Left-hand input used for comparison or differential execution."
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Right-hand input used for comparison or differential execution."
                                 }
                   },
    "required":  [
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_javascript_export_shapes

Compare static return shapes for one exact module/export selector on each authenticated JavaScript Application Graph supplied as inline Evidence or exact same-session retained references. Variants pair only by reciprocal unique literal discriminants. Each retained variant lists observed return-property names even when values stay unknown. Presence (present, absent, or unknown-coverage) is separate from value resolution: a name present on only one paired shape is added or removed when parent-property coverage is complete on both sides, including when the static value is unresolved. Dynamic values, incomplete parent coverage, and unpaired variants remain unknown. Reports JSON Pointer changes without executing JavaScript; runtime behavior requires a separate agent-run probe.

```json
{
    "type":  "object",
    "properties":  {
                       "left_module_path":  {
                                                "type":  "string",
                                                "description":  "Local filesystem path for left module."
                                            },
                       "left_export_name":  {
                                                "type":  "string"
                                            },
                       "right_module_path":  {
                                                 "type":  "string",
                                                 "description":  "Local filesystem path for right module."
                                             },
                       "right_export_name":  {
                                                 "type":  "string"
                                             },
                       "left":  {
                                    "type":  "object",
                                    "description":  "Left-hand input used for comparison or differential execution."
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Right-hand input used for comparison or differential execution."
                                 }
                   },
    "required":  [
                     "left_module_path",
                     "left_export_name",
                     "right_module_path",
                     "right_export_name",
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_managed_members

Compare two authenticated inspect_managed_members Evidence records using unique-only tiers: exact declared type, name, and CIL/signature; exact declared type, name, and signature (decoded or not); exact declared type, name, and decoded structural method shape; then exact CIL/signature or decoded structural method shape alone, which never takes a member whose own declared type and name remain unpaired on the other side or whose declaring type is unresolved. Signatures compare type operands by name, not by build-local metadata row. Names alone are never a matching basis; a member whose signature was not decoded, or whose same-named counterpart's was not, stays unknown rather than added or removed. Matched methods also compare aligned call-site shapes and observed target declaring-type/method names; missing or unaligned call edges and target names remain unknown. These static names do not prove overload identity or runtime dispatch. Metadata tokens remain build-local coordinates bound to each artifact SHA-256 and MVID, and the tuple digest does not itself remap them.

```json
{
    "type":  "object",
    "properties":  {
                       "left":  {
                                    "type":  "object",
                                    "description":  "Left-hand input used for comparison or differential execution."
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Right-hand input used for comparison or differential execution."
                                 }
                   },
    "required":  [
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_process_captures

Compare two compatible process capture observations across terminal, interaction, lifecycle, process, filesystem, command-shim, HTTP, and WebSocket evidence. Optional trace_spec validates exact events against an explicit partial order or finite trace language; concurrency is never inferred from timestamps or broad sorting. Missing, journal-free, or truncated observations are never treated as equivalent.

```json
{
    "type":  "object",
    "properties":  {
                       "left":  {
                                    "type":  "object",
                                    "description":  "Left-hand input used for comparison or differential execution."
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Right-hand input used for comparison or differential execution."
                                 },
                       "trace_spec":  {
                                          "type":  "object"
                                      },
                       "max_capture_age_ms":  {
                                                  "type":  "integer",
                                                  "description":  "Maximum permitted capture age ms for this operation."
                                              }
                   },
    "required":  [
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_source_to_bundle

Compare a cryptographically committed HistoricalSourceGraph supplied inline or as reference {kind:file,path:absolute-local-JSON-path} with authenticated JavaScript Application Graph Evidence supplied inline or by exact same-session retained reference. Omitted view preserves the full report. Optional view {kind:summary} or {kind:source-to-bundle,collection:mappings|candidates,source_paths?,mapping_ids?,statuses?,fields?,offset?,limit?} retains the full parent and returns selected facts. Query it again using inspect_analysis_view. Empty filters select none; identity fields always remain. Candidate signals are opt-in; classifications use the full comparison. Page coverage never upgrades source coverage. Uses explicit exact-digest, source-map path, current-path, suffix, and basename signals with stable weights. Classifies unchanged, modified, removed, split, merged, duplicated, and unknown; incomplete coverage and ambiguous weak signals never become absence or forced matches.

```json
{
    "type":  "object",
    "properties":  {
                       "reference":  {
                                         "type":  "object"
                                     },
                       "application":  {
                                           "type":  "object"
                                       },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "reference",
                     "application"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_wasm_interfaces

Compare typed core WASM import/export names, kinds and exact signatures from two authenticated inspect_wasm_artifact Evidence records. Select imports, exports or both explicitly. Retained references resolve only in the current session; CLI accepts portable inline Evidence. Duplicate names, unsupported types and missing historical interface facets remain unknown. Preserve original module digests and every occurrence; no source acquisition, module execution or runtime-compatibility inference.

```json
{
    "type":  "object",
    "properties":  {
                       "left":  {
                                    "type":  "object",
                                    "description":  "Exact complete inspect_wasm_artifact Evidence or retained session reference"
                                },
                       "right":  {
                                     "type":  "object",
                                     "description":  "Exact complete inspect_wasm_artifact Evidence or retained session reference"
                                 },
                       "scope":  {
                                     "type":  "string",
                                     "description":  "Explicit interface selection; static equality does not establish runtime compatibility"
                                 }
                   },
    "required":  [
                     "left",
                     "right",
                     "scope"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_web_captures

Compare passive web captures by providing before and after, each with the complete inspect_web_page normalized_result in inspection and an optional complete discover_webmcp_tools normalized_result in webmcp; or compare recorded scenarios by passing complete capture_browser_scenario normalized_result objects in before_scenario and after_scenario with an optional normalization policy. Use exactly one input group. Network comparison includes observed HTTP requests and WebSocket frame streams; same-URL connections are compared as an unordered collection of ordered streams, retaining connection counts without claiming stable identity between individual connections. Scenario comparison aligns exact step IDs, records deterministic literal normalization, and returns every artifact-level difference plus alignment failures inline. Missing or truncated evidence never proves equality.

```json
{
    "type":  "object",
    "properties":  {
                       "before":  {
                                      "type":  "object",
                                      "description":  "The earlier or left-hand observation to compare."
                                  },
                       "after":  {
                                     "type":  "object",
                                     "description":  "The later or right-hand observation to compare."
                                 },
                       "before_scenario":  {
                                               "type":  "object",
                                               "description":  "Earlier complete normalized_result from capture_browser_scenario."
                                           },
                       "after_scenario":  {
                                              "type":  "object",
                                              "description":  "Later complete normalized_result from capture_browser_scenario."
                                          },
                       "normalization":  {
                                             "type":  "object",
                                             "description":  "Exact-literal rules committed in the comparison result."
                                         }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Provide a complete input group: before + after; before_scenario + after_scenario + [normalization]. Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "minProperties":  2,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## compare_web_screenshots

Compare two self-verifying PNG screenshot artifacts with local pixel metrics. Returns exact changed-pixel ratios and channel deltas without OCR or external services.

```json
{
    "type":  "object",
    "properties":  {
                       "before":  {
                                      "type":  "object",
                                      "properties":  {
                                                         "sha256":  {
                                                                        "type":  "string",
                                                                        "pattern":  "^[a-f0-9]{64}$"
                                                                    },
                                                         "bytes":  {
                                                                       "type":  "integer",
                                                                       "minimum":  1,
                                                                       "maximum":  9007199254740991
                                                                   },
                                                         "media_type":  {
                                                                            "type":  "string",
                                                                            "const":  "image/png"
                                                                        },
                                                         "data_base64":  {
                                                                             "type":  "string"
                                                                         }
                                                     },
                                      "required":  [
                                                       "sha256",
                                                       "bytes",
                                                       "media_type",
                                                       "data_base64"
                                                   ]
                                  },
                       "after":  {
                                     "type":  "object",
                                     "properties":  {
                                                        "sha256":  {
                                                                       "type":  "string",
                                                                       "pattern":  "^[a-f0-9]{64}$"
                                                                   },
                                                        "bytes":  {
                                                                      "type":  "integer",
                                                                      "minimum":  1,
                                                                      "maximum":  9007199254740991
                                                                  },
                                                        "media_type":  {
                                                                           "type":  "string",
                                                                           "const":  "image/png"
                                                                       },
                                                        "data_base64":  {
                                                                            "type":  "string"
                                                                        }
                                                    },
                                     "required":  [
                                                      "sha256",
                                                      "bytes",
                                                      "media_type",
                                                      "data_base64"
                                                  ]
                                 },
                       "channel_threshold":  {
                                                 "type":  "integer",
                                                 "minimum":  0,
                                                 "maximum":  255
                                             }
                   },
    "required":  [
                     "before",
                     "after"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## correlate_static_and_runtime

Evaluate every explicit caller-declared hypothesis between exact static comparison findings and runtime comparison dimensions. Similar names or paths are never auto-matched, consistent cochange never proves causality, and unknown or truncated inputs remain unresolved. Returns all correlations and their complete Evidence closure inline.

```json
{
    "type":  "object",
    "properties":  {
                       "static_comparisons":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  [
                                                                             "null",
                                                                             "boolean",
                                                                             "object",
                                                                             "array",
                                                                             "number",
                                                                             "string"
                                                                         ],
                                                                "items":  {

                                                                          }
                                                            }
                                              },
                       "runtime_comparisons":  {
                                                   "type":  "array",
                                                   "items":  {
                                                                 "type":  [
                                                                              "null",
                                                                              "boolean",
                                                                              "object",
                                                                              "array",
                                                                              "number",
                                                                              "string"
                                                                          ],
                                                                 "items":  {

                                                                           }
                                                             }
                                               },
                       "mappings":  {
                                        "type":  "array",
                                        "items":  {
                                                      "type":  [
                                                                   "null",
                                                                   "boolean",
                                                                   "object",
                                                                   "array",
                                                                   "number",
                                                                   "string"
                                                               ],
                                                      "items":  {

                                                                }
                                                  }
                                    }
                   },
    "required":  [
                     "static_comparisons",
                     "runtime_comparisons",
                     "mappings"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## current_address

Return Hopper's current cursor address for the selected document. Use only for interactive navigation state; prefer explicit addresses in reproducible investigations.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## current_document

Return the document currently selected by REA's Hopper bridge.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## current_procedure

Return the analyzed procedure containing Hopper's current cursor. This uses GUI cursor state and is not a procedure-name lookup.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## cutter_command

Run one arbitrary Rizin command in a selected live Cutter session. Commands may edit and persist analysis state or execute plugin-provided effects; completion uncertainty is returned as Evidence and must not be retried automatically. The authenticated request and response frame is bounded by REA_BRIDGE_MAX_REQUEST_BYTES (default 16777216 bytes, floor 7464, maximum 536870887). The payload budget excludes the terminating LF. An out-of-band Cutter process without the setting uses 16777216 bytes; a mismatch is refused before the command is sent. In a shared MCP server, this admitted command closes any active binary-analysis session; after its effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      },
                       "expected_generation":  {
                                                   "type":  "integer",
                                                   "minimum":  0,
                                                   "maximum":  9007199254740991
                                               },
                       "command":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "json":  {
                                    "type":  "boolean"
                                }
                   },
    "required":  [
                     "session_id",
                     "expected_generation",
                     "command"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## decode_android_resources

Decode one local APK's resources with `apktool d --no-src` in a provider-owned workspace that is removed after projection: the readable AndroidManifest.xml, apktool.yml version and SDK facts, the app's string resources (default or selected locales, bounded to 2000 entries per table), selectable string locales, and the decoded file count. The target APK is digest with SHA-256 before decoding; smali is skipped because Java-source recovery belongs to the JADX family. Optional decode_timeout_ms is the decode wall clock in milliseconds (default 600000, range 1..2147483647); omitted means 600000 and the value applies on the next decode. It does not raise the fixed output capture allowance or the version probe.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "include_strings":  {
                                               "type":  "boolean"
                                           },
                       "string_tables_only":  {
                                                  "type":  "boolean"
                                              },
                       "locale":  {
                                      "type":  "string",
                                      "minLength":  2,
                                      "maxLength":  23,
                                      "pattern":  "^(?:[a-z]{2,3}(?:-(?:r?[A-Z]{2}|[0-9]{3}))?|b\\+[a-z]{2,3}(?:\\+[A-Za-z0-9]{2,8})*)$"
                                  },
                       "locales":  {
                                       "minItems":  1,
                                       "maxItems":  20,
                                       "type":  "array",
                                       "items":  {
                                                     "type":  "string",
                                                     "minLength":  2,
                                                     "maxLength":  23,
                                                     "pattern":  "^(?:[a-z]{2,3}(?:-(?:r?[A-Z]{2}|[0-9]{3}))?|b\\+[a-z]{2,3}(?:\\+[A-Za-z0-9]{2,8})*)$"
                                                 }
                                   },
                       "decode_timeout_ms":  {
                                                 "type":  "integer",
                                                 "minimum":  1,
                                                 "maximum":  2147483647
                                             }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## decode_interface_builder

Decode compiled storyboard and nib archives inside the active Apple app bundle, including keyed property lists and NIBArchive object tables, into a bounded object, hierarchy, and connection graph. This is static archive parsing; unrecognized archive classes and unresolved code handlers remain explicit unknowns. A full app graph is often several megabytes; detail summary retains it and returns documents with node, edge and coverage counts, then page rows, optionally one document's, with inspect_analysis_view kind interface-builder.

```json
{
    "type":  "object",
    "properties":  {
                       "max_documents":  {
                                             "type":  "integer",
                                             "minimum":  1,
                                             "maximum":  64
                                         },
                       "max_objects":  {
                                           "type":  "integer",
                                           "minimum":  1,
                                           "maximum":  20000
                                       },
                       "max_connections":  {
                                               "type":  "integer",
                                               "minimum":  1,
                                               "maximum":  40000
                                           },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## decode_signal_recording

Decode an existing local logic/signal recording on Linux with sigrok-cli 0.7.2 and util-linux prlimit. Supply decoder stack, channels and options explicitly; optionally select input format and annotation classes. Return 50 literal output lines by default, with line_offset/line_limit paging; line_limit:null requests all remaining lines. Output windows count display lines, not annotations or samples, and do not shorten decoding. Preserve selected text, sample ranges, stderr, source/tool identity and coverage inline. Configure absolute REA_SIGROK_COMMAND. File-only analysis; no hardware acquisition or device communication. Empty output does not prove absence of traffic.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local recording path"
                                },
                       "decoders":  {
                                        "type":  "string",
                                        "description":  "sigrok decoder stack, options and channel assignments, e.g. uart:rx=RX:baudrate=9600"
                                    },
                       "input_format":  {
                                            "type":  [
                                                         "string",
                                                         "null"
                                                     ],
                                            "description":  "Explicit sigrok input format and options; omitted means autodetection"
                                        },
                       "annotations":  {
                                           "type":  [
                                                        "string",
                                                        "null"
                                                    ],
                                           "description":  "Optional annotation selection, e.g. uart=rx-data"
                                       },
                       "line_offset":  {
                                           "type":  "integer",
                                           "description":  "Skip this many literal LF-separated output lines; default 0"
                                       },
                       "line_limit":  {
                                          "type":  [
                                                       "integer",
                                                       "null"
                                                   ],
                                          "description":  "Return at most this many output lines; default 50. Null explicitly returns all remaining lines within producer limits. Lines are not annotation counts."
                                      }
                   },
    "required":  [
                     "path",
                     "decoders"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## decompile_all

Write provider pseudocode for every analyzed function, or a filtered subset, into a caller-selected directory and return only a manifest. Each file records the provider function name, canonical address, inclusive body ranges when procedure_info supplies them, and that function's procedure_info and pseudocode limitations. Each function's index record keeps its complete limitations. Evidence keeps at most eight first-observed distinct per-function limitations within an encoded-text budget; the omitted count measures observations, not distinct warnings. The manifest reports counts, the output directory, the index path, at most 32 failure details within 2,048 JSON-encoded UTF-8 bytes (names can be omitted; full names remain in the index), provider identity, and the analysis-profile digest. Pseudocode stays in the files. An existing directory is refused unless resume is set and the journal matches the target SHA-256, provider, and analysis profile; verified files are skipped and conflicting files are left unchanged. layout dwarf reports that compilation-unit paths are unavailable and names files by address. A failure of one function does not stop the others. Functions procedure_info classifies as external are counted as external, get no file, and are not decompiled; a resume skips them. Cancellation leaves files already written. Per-function limits do not bound the whole call; use limit and resume to select work that fits the client deadline, including import, listing and file work.

```json
{
    "type":  "object",
    "properties":  {
                       "output_directory":  {
                                                "type":  "string",
                                                "description":  "Absolute output directory. The parent must exist. The directory itself must be absent unless resume is true. One run at a time may use a directory; a second run is refused while the first holds it."
                                            },
                       "layout":  {
                                      "type":  "string",
                                      "description":  "address names files by canonical address. dwarf asks for compilation-unit paths and is reported unavailable when the provider has no such fact."
                                  },
                       "resume":  {
                                      "type":  "boolean",
                                      "description":  "Continue a journal for the same target SHA-256, provider, analysis profile, and layout. Both digests must be known. Verified files and functions recorded as external are skipped, conflicting files are left unchanged, and functions not yet tried run before retries of recorded failures."
                                  },
                       "name":  {
                                    "type":  "string",
                                    "description":  "Case-sensitive literal substring of the provider function name."
                                },
                       "namespace":  {
                                         "type":  "string",
                                         "description":  "Case-sensitive prefix of the provider function name, including any separator such as Foo:: or Class."
                                     },
                       "address_start":  {
                                             "type":  "string",
                                             "description":  "Inclusive lower bound as 0xoffset or space:0xoffset. Must use the same address space as address_end."
                                         },
                       "address_end":  {
                                           "type":  "string",
                                           "description":  "Inclusive upper bound as 0xoffset or space:0xoffset. Must use the same address space as address_start."
                                       },
                       "limit":  {
                                     "type":  "integer",
                                     "description":  "Maximum functions to attempt on this call after skipping verified files and functions recorded as external. Further matches remain for a later resume."
                                 }
                   },
    "required":  [
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## decompile_jeb_item

Decompile one type or method to engine pseudo-code using its decompiler (Dalvik, Java, or native). item_address comes from the engine's code item listing; unit_path defaults to the engine's first code unit and stays unknown when omitted. Read-only with respect to the target file.

```json
{
    "type":  "object",
    "properties":  {
                       "unit_path":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "item_address":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "item_kind":  {
                                         "type":  "string",
                                         "enum":  [
                                                      "type",
                                                      "method"
                                                  ]
                                     }
                   },
    "required":  [
                     "item_address",
                     "item_kind"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## demangle_swift

Demangle an ordered list of single-line Swift symbols without requiring Hopper. Each input returns demangled, unchanged, or invalid status. An exact underscore input skipped by swift-demangle is invalid with empty output; it does not discard valid companion results. native_command_timeout_ms is the swift-demangle wall clock (omitted means 60000 ms) and does not raise the fixed 67108864-byte output allowance.

```json
{
    "type":  "object",
    "properties":  {
                       "symbols":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 },
                                       "description":  "Ordered Swift symbols to demangle."
                                   },
                       "native_command_timeout_ms":  {
                                                         "type":  "integer",
                                                         "description":  "Wall-clock budget in milliseconds for each macOS native command in this call (otool, lipo, codesign, plutil, swift-demangle, and the other allowlisted Xcode tools). Omitted means 60000. Integer from 1 to 2147483647. Applies on the next call. Does not raise the fixed 67108864-byte output allowance or the 65536-byte failure-diagnostic excerpt. Named native_command_timeout_ms rather than command_timeout_ms because command_timeout_ms is the separate one-shot objdump and rizin deadline (default 120000)."
                                                     }
                   },
    "required":  [
                     "symbols"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## discover_webmcp_tools

Passively inventory every page-registered WebMCP tool and its complete structural input-schema summary using the experimental CDP WebMCP domain. Metadata is page-declared-untrusted; REA never registers or invokes discovered tools.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "observation_ms":  {
                                              "type":  "integer",
                                              "minimum":  0,
                                              "maximum":  9007199254740991
                                          },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## evaluate_reconstruction_coverage

Evaluate inline evidence-backed reconstruction coverage against one named boundary. Missing ownership or inventory is partial; stale, weak, truncated, skipped, unresolved, or conflicting tied verifier proof is unknown; contradictions, failed proof, missing owners, and authority routing fail closed.

```json
{
    "type":  "object",
    "properties":  {
                       "coverage":  {
                                        "type":  "object",
                                        "description":  "Exact reconstruction-coverage commitment to verify."
                                    },
                       "boundary_id":  {
                                           "type":  "string",
                                           "description":  "Exact reconstruction boundary identifier to evaluate."
                                       }
                   },
    "required":  [
                     "coverage",
                     "boundary_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## execute_rizin_command

Execute one caller-selected Rizin command against a local artifact using the Rizin CLI profile that suppresses user settings and scripts while retaining plugins. Preserves decoded stdout and stderr inline with SHA-256 Evidence, up to a shared 16 MiB combined limit; invalid UTF-8 bytes are represented by replacement characters and flagged in Evidence limitations. output_truncated reports output beyond that limit and completion_status is unknown if buffer enforcement terminated the process. Rizin commands and retained plugins can execute foreign code, mutate files, access the network, and read files outside the selected artifact; identities and contents of those external inputs are not bound to Evidence. Command effects depend on the Rizin command and loaded plugins; JSON is command-specific and raw output remains authoritative. In a shared MCP server, this admitted command closes any active binary-analysis session; after its effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first. Optional command_timeout_ms is the per-phase wall clock in milliseconds (default 120000, range 1..2147483647); omitted means 120000 and the value applies on the next call. It does not raise the 16 MiB output cap or a Rizin debugger session deadline.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "command":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              }
                   },
    "required":  [
                     "path",
                     "command"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## export_environment_bundle

Write an agent environment bundle folder from capture Evidence (capture_browser_scenario, or HAR inspect_web_network_capture Evidence) into an absent absolute output_directory whose parent exists, as rea environment-build does. It projects the environment manifest with the default template rules, derives the state the bundle serves, and reports read tools answered from the state or replayed with their reason, and write tools served or withheld with a hint about what to record next; check the same coverage first with inspect_analysis_view view environment-coverage. dry_run reports without writing. The bundle copies the capture Evidence byte for byte, recorded response bodies included, and its README names how the agentenv framework uses it. Reads that Evidence, and package_tarball when given, and writes only that folder; no network access. dry_run still checks output_directory. Destination refusals name this tool; the others name their stage as environment-build's do.

```json
{
    "type":  "object",
    "properties":  {
                       "source":  {
                                      "type":  "object",
                                      "description":  "The capture the environment is built from: capture_browser_scenario Evidence, or HAR inspect_web_network_capture Evidence."
                                  },
                       "output_directory":  {
                                                "type":  "string",
                                                "description":  "Absolute, absent output directory for the bundle folder; parent must exist."
                                            },
                       "name":  {
                                    "type":  "string",
                                    "description":  "Env folder and env id (kebab-case). Default: the manifest\u0027s name; required when the capture\u0027s origin is a loopback or IP address."
                                },
                       "template_rules":  {
                                              "type":  "array",
                                              "items":  {
                                                            "type":  [
                                                                         "null",
                                                                         "boolean",
                                                                         "object",
                                                                         "array",
                                                                         "number",
                                                                         "string"
                                                                     ],
                                                            "items":  {

                                                                      }
                                                        },
                                              "description":  "Opt-in template rules, each at most once; [] applies only the format rules. Default: after-uid-literal, opaque-token, sibling-variance and prefix-variance."
                                          },
                       "package_tarball":  {
                                               "type":  "string",
                                               "description":  "Absolute path of a packed rea-agents tarball the bundle installs instead of the registry package of this version."
                                           },
                       "dry_run":  {
                                       "type":  "boolean",
                                       "description":  "Report what the bundle would hold without writing anything."
                                   }
                   },
    "required":  [
                     "source",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## export_evidence_bundle

Atomically write the session's deterministic Evidence bundle to the requested local path. Existing files require overwrite: true; records and manifests use canonical byte-stable ordering.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local filesystem path for the exported evidence bundle; relative paths are rejected."
                                },
                       "overwrite":  {
                                         "type":  "boolean",
                                         "description":  "Whether an existing destination may be replaced."
                                     }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## export_web_scripts

Export JavaScript bytes already retained in one local inspect_web_page or capture_browser_scenario JSON capture (normalized result or complete Evidence). Writes scripts and a digest-verified manifest into an absent absolute output_directory. Returns all source URLs, script or transaction/event references, unavailable states, and an analysis_input for analyze_javascript_application when any bytes were exported. Include script sources in inspect_web_page or select capture.network.response_body in scenarios. No refetch or execution. Safe unambiguous URL layouts preserve relative module paths; versions, query variants, inline scripts, and path collisions are isolated with explicit resolution limitations.

```json
{
    "type":  "object",
    "properties":  {
                       "capture_path":  {
                                            "type":  "string",
                                            "minLength":  1,
                                            "pattern":  "^[^\\x00]*$"
                                        },
                       "output_directory":  {
                                                "type":  "string",
                                                "minLength":  1,
                                                "pattern":  "^[^\\x00]*$"
                                            }
                   },
    "required":  [
                     "capture_path",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## extract_artifact

Extract regular files (including Node.js SEA scripts and assets) exposed by a supported active archive reader into a fresh temporary directory chosen by REA. A nested archive exposed as a file is copied as a file; inspect and extract it separately to unpack its contents. The result includes its location. DMG extraction is unavailable; use a caller-managed read-only mount on macOS to access its files. Rejects traversal and symlink escapes, never overwrites, and verifies cleanup. Integrity mismatches fail by default; record-and-continue extracts the observed bytes and returns each mismatch as an explicitly untrusted integrity contradiction.

```json
{
    "type":  "object",
    "properties":  {
                       "integrity_policy":  {
                                                "type":  "string",
                                                "enum":  [
                                                             "fail",
                                                             "record-and-continue"
                                                         ]
                                            }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## extract_firmware

Extract caller-selected firmware bytes with Unblob 26.6.x (verified with 26.6.4) into an absent output directory. Returns verified child paths/digests, task derivations, unknown chunks and uncovered selected input ranges, extraction diagnostics and explicit extraction_gaps rows naming unextracted regions, missing extractor dependencies and a continuation_id referencing shared extraction_continuations recovery instructions. Preserves the default sandbox; publishes regular files only. Linux, caller-supplied tool, format-specific extractors, util-linux prlimit and procps ps required. No mounts or extracted-code execution. Select a returned native file with open_binary for Ghidra analysis; decompressed offsets are not original-file addresses.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local firmware file; never executed. Relative paths are rejected."
                                },
                       "analysis_timeout_ms":  {
                                                   "type":  "integer",
                                                   "description":  "Analysis wall-clock budget in milliseconds (default 120000), from tool resolution through inspection and its final workspace cleanup, or extraction through inventory. Excludes queue admission and extraction publication. Does not raise fixed CPU, memory or byte limits."
                                               },
                       "output_directory":  {
                                                "type":  "string",
                                                "description":  "Absolute, absent output directory; parent must exist"
                                            },
                       "range":  {
                                     "type":  "object",
                                     "description":  "Explicit file-byte interval; omit to extract the whole file"
                                 },
                       "max_depth":  {
                                         "type":  "integer",
                                         "description":  "Extraction depth; reaching it yields partial analysis"
                                     },
                       "max_output_bytes":  {
                                                "type":  "integer",
                                                "description":  "Staging file-byte budget, checked during extraction; transient overshoot is possible"
                                            },
                       "max_output_files":  {
                                                "type":  "integer",
                                                "description":  "Staging entry budget, including directories and links"
                                            },
                       "publication_timeout_ms":  {
                                                      "type":  "integer",
                                                      "description":  "Publication and final cleanup budget in milliseconds, started after extraction and inventory; increase for a slow destination"
                                                  }
                   },
    "required":  [
                     "path",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## find_changed_behavior

Aggregate validated process, artifact, and function comparison Evidence. Runtime observations remain distinct from static behavior candidates; missing or incomplete comparisons produce unresolved findings, never causal claims. Returns every finding inline.

```json
{
    "type":  "object",
    "properties":  {
                       "comparisons":  {
                                           "type":  "array",
                                           "items":  {
                                                         "type":  [
                                                                      "null",
                                                                      "boolean",
                                                                      "object",
                                                                      "array",
                                                                      "number",
                                                                      "string"
                                                                  ],
                                                         "items":  {

                                                                   }
                                                     },
                                           "description":  "Validated comparison Evidence records to aggregate."
                                       }
                   },
    "required":  [
                     "comparisons"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## find_xrefs_to_name

Resolve an exact name against the bound provider's name inventory and return a resolved or unresolved result. Unresolved names use the stable name_not_found reason; this xref workflow returns address-only projections.

```json
{
    "type":  "object",
    "properties":  {
                       "name":  {
                                    "type":  "string"
                                }
                   },
    "required":  [
                     "name"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## frida_session_status

Read observed Frida session state on each call. Default detail=complete returns queued script messages and capture truncation. detail=summary retains the complete observation in this session and returns identity, state, scripts, retained message count and truncation without payloads. Export the complete parent with export_evidence_bundle; retained summaries are historical and never refresh live state.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## gdb_console

Run one unrestricted GDB CLI command through MI interpreter-exec console and return raw MI, console, target, and log records inline with Evidence. Default detail complete preserves the original Evidence. Explicit detail summary retains that complete parent and returns every ordered MI record once, omitting duplicate stream groupings and raw mirrors; parent_evidence_id supports later inspect_analysis_view summary queries and complete Evidence export. A command timeout preserves collected records with completion_status unknown and stops the owned debugger session. Commands can execute shell, alter target state, access files, and use network. Closing the debugger never implies target termination. In a shared MCP server, this admitted command closes any active binary-analysis session; after its effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      },
                       "command":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "session_id",
                     "command"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## gdb_session_status

Return the observed lifecycle state and bounded recent MI records and process diagnostics for one persistent REA GDB session; truncation and invalid-UTF-8 fields report discarded or lossy data.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## get_call_graph

Resolve one symbol or address to its canonical procedure entry, then traverse the bound provider's caller or callee relationships until the reachable graph is exhausted. Every node uses a canonical address and has an ok/error status; failures use safe typed projections. Unresolved indirect calls may be missing and results are not a whole-program CFG.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "direction":  {
                                         "type":  "string",
                                         "enum":  [
                                                      "forward",
                                                      "backward"
                                                  ]
                                     }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## get_evidence_bundle

Return every retained Evidence record and residual unknown as a complete canonical bundle by default. detail=summary discovers retained records without copying or transferring their payloads; optionally filter by exact Evidence ID, operation, target SHA-256, analysis-profile digest or canonical native procedure address. Metadata distinguishes complete record retention from native value-flow truncation or unavailable/unknown coverage. Use a discovered evidence_id with inspect_analysis_view without restarting a provider. Filters apply only to summary delivery.

```json
{
    "type":  "object",
    "properties":  {
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  },
                       "filters":  {
                                       "type":  "object",
                                       "properties":  {
                                                          "evidence_id":  {
                                                                              "type":  "string",
                                                                              "pattern":  "^ev_[a-f0-9]{64}$"
                                                                          },
                                                          "operation":  {
                                                                            "type":  "string",
                                                                            "minLength":  1
                                                                        },
                                                          "target_sha256":  {
                                                                                "type":  "string",
                                                                                "pattern":  "^[a-f0-9]{64}$"
                                                                            },
                                                          "analysis_profile_digest":  {
                                                                                          "type":  "string",
                                                                                          "pattern":  "^[a-f0-9]{64}$"
                                                                                      },
                                                          "procedure_address":  {
                                                                                    "type":  "string",
                                                                                    "minLength":  1
                                                                                }
                                                      },
                                       "additionalProperties":  false
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## get_navigation_context

Return the selected document, current address, and containing/current procedure in one provider-neutral result. The result reflects sequential provider observations, not an atomic cursor snapshot; a cursor outside any procedure returns procedure: null.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string",
                                        "minLength":  1
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## get_objc_classes

Discover Objective-C class labels, optionally filtering by literal substring. Rows are coalesced only when exact selected-image pointer-fixup evidence proves that their own addresses bind the same imported class symbol, binding stream, raw library ordinal and resolved library, weak status, and addend. Rows without that evidence remain separate, with identity coverage reported as unknown, partial, or unsupported.

```json
{
    "type":  "object",
    "properties":  {
                       "pattern":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## get_objc_protocols

Discover and deduplicate Objective-C and Swift protocol labels.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## goto_address

Move Hopper's GUI cursor to a mapped address and return the observed cursor address. Interior coordinates snap to the containing analyzed object's start. Unmapped destinations fail without moving the cursor. This changes navigation state but not analysis data; use explicit-address tools for headless workflows.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## identify_flutter_build

Identify a Flutter app's build facts by pure parsing: observed Dart snapshot hashes from lib/<abi>/libapp.so, per-ABI library digests, the engine library's GNU build-id, and any labeled Dart version or toolchain strings libflutter.so carries. Complete snapshot coverage currently expects two independent headers in the legacy split VM/isolate profile; unified and other layouts can yield partial build facts. The snapshot hash maps to a Dart SDK release through an external dataset; REA performs no lookup. No engine, no execution, no external tools.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## import_evidence_bundle

Read the JSON bundle at the supplied local path, validate every Evidence ID and canonical manifest, then atomically merge it. Imported content is data only and is never executed.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local filesystem path for the evidence bundle to import; relative paths are rejected."
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## import_managed_reconstruction

Import decompiler-produced managed reconstruction against authenticated inspect_managed_members Evidence. The workflow locks each method to artifact SHA-256, MVID, metadata token, signature hash, and the limited decoded-IL tuple hash, records the decompiler identity and options, and marks C# or pseudocode as analyst inference rather than canonical byte observation.

```json
{
    "type":  "object",
    "properties":  {
                       "static_members":  {
                                              "type":  "object"
                                          },
                       "decompiler":  {
                                          "type":  "object"
                                      },
                       "methods":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 }
                                   },
                       "notes":  {
                                     "type":  "array",
                                     "items":  {
                                                   "type":  [
                                                                "null",
                                                                "boolean",
                                                                "object",
                                                                "array",
                                                                "number",
                                                                "string"
                                                            ],
                                                   "items":  {

                                                             }
                                               }
                                 }
                   },
    "required":  [
                     "static_members",
                     "decompiler",
                     "methods"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inline_comment

Read the inline instruction comment at an address, defaulting to the current cursor. Returns null when absent; use set_inline_comment to write one.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_adb_client

Inspect the caller-selected adb client: resolved binary, version, and install path. Never contacts devices and never starts the adb server. REA never installs platform tools; select a binary with REA_ADB_PATH (absolute) or adb on PATH.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_adb_device

Inspect one device's build identity through a fixed `getprop` whitelist: Android and SDK versions, security patch, fingerprint, product identity, CPU ABIs, and debuggable flag, plus the observed emulator indicator ro.kernel.qemu. Present properties retain empty values; omitted keys were not reported. Read-only with respect to the device.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_adb_display

Read physical and override size/density from `wm size` and `wm density`. Malformed numeric declarations are retained in numeric_issues with partial coverage; optional values remain null. Required physical-size failures retain partial display facts. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_adb_package

Inspect installed-package versions, install times, installer, user id, flags, and requested-permission count through read-only `dumpsys package`. Malformed numeric declarations are retained in bounded numeric_issues and make coverage partial; version_code stays exact text. Unknown sections are not inferred.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "package":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "maxLength":  256,
                                       "pattern":  "^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)*$"
                                   }
                   },
    "required":  [
                     "serial",
                     "package"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_adb_window

Report the window manager's focus declarations with display context, original lines and explicit summary status. Reads `dumpsys window windows`, falling back to `displays` only when no focus field is declared. Singular fields are unknown for multiple contexts or repeated declarations; inspect focus_contexts for every observation. Read-only; no interaction occurs.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_address_context

Inspect one explicit reproducible address for its analyzed name, containing procedure, regular and inline comments, and matching bookmarks. Each unsupported facet returns a typed unavailable outcome; use xrefs, assembly, or pseudocode for those deeper views.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "document":  {
                                        "type":  "string",
                                        "minLength":  1
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_analysis_settings

Inspect REA's editable runtime admission sizes, deadlines and worker heaps, their bounds, launch values and application points. No active target is required. Filters intersect. If an error already names a setting and its bounds, update_analysis_settings can be called directly. Explicit per-call arguments override runtime defaults. Active worker values are not observed by this configuration view.

```json
{
    "type":  "object",
    "properties":  {
                       "settings":  {
                                        "maxItems":  11,
                                        "type":  "array",
                                        "items":  {
                                                      "type":  "string",
                                                      "enum":  [
                                                                   "asar.max_header_bytes",
                                                                   "ghidra.startup_timeout_ms",
                                                                   "hopper.startup_timeout_ms",
                                                                   "ghidra.decompile_timeout_seconds",
                                                                   "jadx.operation_timeout_ms",
                                                                   "regex.matching_deadline_ms",
                                                                   "regex.worker_heap_request_mib",
                                                                   "capture.decoder_old_generation_mib",
                                                                   "javascript.analysis_timeout_ms",
                                                                   "javascript.max_heap_mb",
                                                                   "wasm.command_timeout_ms"
                                                               ]
                                                  }
                                    },
                       "operation":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "provider":  {
                                        "type":  "string",
                                        "minLength":  1
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_analysis_view

Use javascript-routes collections to query route patterns, source-linked occurrences, source status, unknowns or summary counts from retained JavaScript application or web-bundle Evidence. Inspect selected views of supported completed Evidence without repeating analysis. Choose a view compatible with the parent operation described below. Command summary views preserve ordered GDB records or opaque objdump stdout/stderr once, source parameters, completion facts and parent identity. They do not establish current target state. Historical capture summaries return identity and counts without bodies or archive inventories. Capture views page retained records: view {kind: "capture", detail: "preview" | "complete", offset: 0, limit: 20}; preview selects recorded headline fields and binary descriptors, complete returns exact records with all sidecars. Offsets follow the selected parent record order, preserving original producer ordinals. Paging never reopens the source or reruns the decoder. Native export tables use kind native-exports, collection exports/issues, exact names or original indices, selected fields and matching-row pagination; parent Evidence plus source_index identifies each row. Source coverage stays separate from page coverage. BEAM views use kind beam, collections atoms/exports/imports/lambdas/chunks, exact names/indices/fields and matching-row pages; import modules intersects function names. source_index identifies original parent-and-collection rows, preserving every arity and raw atom index. beam.counts preserves null missing tables versus decoded empty tables; runtime absence remains unknown. PYC views use kind python-pyc, collection code_objects, names/indices/fields and paging; source_index scopes every same-name alternative to the parent, nested_code keeps code-constant references, and pyc.coverage preserves source coverage. Go metadata uses kind go, collection dependencies/settings, exact paths/keys or indices, fields and pagination. Row index refers to the original collection; empty selections return no rows, and dependency coverage remains unknown. Rust metadata uses kind rust, collection packages/edges, exact names/versions or original indices, selected fields, edge from_indices/to_indices and matching-row pagination. Embedded-record coverage is separate from unknown runtime dependencies. For inspect_pcap_capture parents, kind pcap pages endpoints, dns-queries, tls-sni, protocols, http-uris or unattributed-addresses. Select an exact query for dns-addresses, or a tshark/capinfos stream for UTF-16 diagnostics text. Page totals count retained facts; source coverage remains explicit. Qlog summaries keep profile/scope and counts; kind qlog pages traces, streams, loss-declarations, incomplete-records, contributions, ranges or final-sizes. Except traces, select trace_index; stream-specific facets also require exact stream_id and direction. Facts retain all-recorded-events-before-selection scope, and interval overlap or endpoint loss declarations do not prove retransmission or network loss. The api-surface view groups retained HAR requests by method, origin and format-rule path template; templated segments are inferences. It reads no headers or bodies and reports no query values. The environment-manifest view projects the same HAR requests on the primary origin (most JSON-like records) into a replayable tool surface: view {kind: "environment-manifest", template_rules: ["after-uid-literal", "opaque-token", "sibling-variance"], include_query_parameter_names: true}. Each tool has a deterministic name, a path template and parameters from the four format rules plus the selected opt-in rules, observed query parameter names, a value-free body shape for POST/PUT/PATCH, status and content-type sets and record ordinals; records are pointers into the parent, excluded groups carry a reason and three fixed unknowns are always stated. Templates, parameters and names are inferences; the view copies no header, cookie or body values and refuses parents inspected with sensitive values. A capture_browser_scenario parent that selected response bodies or header values projects the same way, one record per network transaction in request order; it relies on capture-time redaction of declared secrets instead of that refusal, and a transaction whose URL was redacted is left unrepresented. The environment-state view extracts, from the same parents, the initial state the recorded reads observed: view {kind: "environment-state", template_rules: [...]} with the rules of the manifest it accompanies. It reads response bodies recorded before the first related write and reports entity types with an identity field (proved by a path parameter or inferred from a field name), entities in collection order with per-view values where views differ, documents for non-parameterized reads that are not entities and have one initial request line (item-shaped paths stay recorded), and a render plan per read tool: item and list views reproduce their latest recordings from the state (a list also learns the filters, search, sort and paging its recordings verify, and answers new values of them from the state), and every other read tool is a recorded view with a reason. It also reports a write plan per state-changing tool (create, update, delete or document, built from its first successful call) and write_clock; a plan is served only when replaying the capture shows a later read confirming its effect and none contradicting it. Types and identity are inferences; values are recorded data, so it refuses parents inspected with sensitive values, and a scenario parent must have selected response bodies; its values keep the capture redaction markers, and a write whose URL it redacted ends the state of every type. The environment-coverage view, view {kind: "environment-coverage", template_rules: [...]}, summarizes that state without its entities: reads.state and reads.recorded count read tools answered from the state and replayed (recorded_reasons per view reason), writes.served lists the served write tools, and writes.withheld lists each other write tool with its reason, plan record, contradicting read and a hint about what to record next; it refuses what the environment-state view refuses. For a retained record use source: {kind: "retained-evidence", evidence_id: "<parent_evidence_id>"}. Use normalized_result.parent_evidence_id from a summary receipt. Source kind is required. Portable source uses {kind: "inline", evidence: <complete Evidence>}. A completed Evidence JSON file on this host uses {kind: "evidence-file", path: "<absolute path>"}: REA reads and authenticates it, then projects it like retained Evidence, so a large capture is bounded by the file reader rather than by inline-input accounting. The file may also be an Evidence bundle written by CLI --evidence_output or export_evidence_bundle; select evidence_id when it contains more than one record. For layout, JavaScript, inventory, WASM, Mach-O, Interface Builder, EVM, managed artifact, Android package and Dart AOT sources, request a summary with view: {kind: "summary"}; summary is a view kind and has no facet. The WASM summary gives validation, counts, WAT digest/length, tool profile and execution/network states without reading WAT text. Select a layout mitigations/linkage facet, one section/symbol/module, or a stable page. Module pages include assets, bundled and source modules; use node_id for ambiguous or unavailable paths. Typed module references use view {kind: "javascript", collection: "modules", projection: "references", where: {path: "main.js", kind: "javascript-module"}, offset: 0, limit: 64}; where matches exact retained strings (null selects unavailable paths). Rows include canonical identity, retained source-range count and observation support states. Source coverage and selection remain explicit; exhausting matching rows does not prove runtime loading or complete analysis. For exact file/native facts and processing gaps, use view {kind: "javascript-facts", where: {paths: ["package.json"]}, property_keys: ["name", "operation", "analysis_status"]}. Rows page original observations with canonical identities and Evidence; missing properties remain omitted. Paths match recorded artifact-path/source-range locations; optional kinds filter nodes. To discover field names without values, opt into include_available_property_keys: true with property_keys: []. Canonical identity and Evidence are separate from properties; keep each node_id/observation_id pair together when composing results. Inventory uses view: {kind: "inventory", collection: "occurrences", logical_paths: ["package.json"], offset: 0, limit: 64}. Inventory views page nodes, occurrences, edges or integrity contradictions. Occurrences accept exact logical_paths and occurrence_ids; nodes accept artifact_ids. Batch filters intersect (omitted means all available, [] means none); fields selects columns while retaining identity links. source_complete describes retained graph membership; source_coverage describes producer coverage, including unknown encrypted PCK directories. Page exhaustion does not prove absence in a partial source. WASM views use kind wasm and facet sections/imports/exports/candidates/glue/wat/headers/details/tool_profile; text facets use UTF-16 code units and source forms remain exact. Mach-O views use kind macho and facet entrypoints/architectures/build_metadata/load_commands/dependencies/imports/exports/segments/provenance; source_exhaustive false means unreturned rows may exist. Interface Builder views use kind interface-builder and collection documents/nodes/edges/coverage; nodes and edges accept an exact document relative_path. EVM summary returns all inferred interface candidates, carrier/decoded identities and explicit unknowns without hex or duplicate stdout. EVM views use kind evm and facet functions/bytecode_hex/stdout/stderr; function pages preserve the original candidate rows, and text offsets/limits count UTF-16 code units (hex has two characters per decoded byte). Exhausting candidate pages does not establish global interface completeness. Managed artifact summaries preserve complete identity/classification, metadata status, module/MVID, frameworks, coverage and limitations without unrelated inventories. Managed pages use kind managed and collection references/resources/attributes, retaining source coverage and build-local token scope; page exhaustion does not establish complete metadata. Managed member summaries keep identity, selection with unmatched names, coverage and per-collection counts; managed-members pages use collection types/exported_types/fields/methods/member_refs/call_edges/field_accesses/user_strings and carry the source selection and coverage. Android package summaries retain all declarations, engine identity and counts without decoded manifest text. Android manifest pages use kind android, facet manifest, offset and limit in UTF-16 code units; source_status preserves partial decoded coverage even when the retained text page is exhausted. Dart AOT summary preserves target/ABI/library identity, snapshot headers and pool lower bounds. String pages use kind dart-aot, collection package_uris/dart_uris/class_like_tokens; source coverage remains partial when the producer says partial. Protobuf views use kind protobuf, collection bodies/frames/fields, with record_ordinals, body_pointers and fields; field pages also select frame_indices, field_ids, field_numbers or schema_names. Byte identity, offset basis and body status remain present. Text and schema_value are opt-in page columns. APK signing views use kind apk-signature and collection signers/digests/lineage, with ids, signer_ids, schemes, fields and paging. Identity links remain when fields is empty. Declared identities and digest matches do not establish signature verification or authenticity. Model views use kind model-artifact and collection metadata (keys), tensors (names/types) or types (derived totals), with indices, fields and paging. Header SHA-256 scopes declarations; full-file identity remains unknown unless full hashing was selected. Arrays retain typed eight-element previews with explicit omissions. IL2CPP metadata views use kind il2cpp-metadata and collection sections/images/types/methods/fields/properties/events/parameters/string_literals/code_gen_modules/method_addresses/return_types/field_types/parameter_types/generic_method_instances/generic_method_issues. Select indices, fields and pages; sections/images/types/methods/fields/properties/events/parameters/return_types/field_types/parameter_types/generic_method_instances accept exact names (native type names match retained rendered values, preserving same-name rows; null names never match), types also accept image_indices/namespaces/declaring_types, and methods/members accept declaring_types. Parameters and parameter_types accept method_indices; code_gen_modules accept image_indices; method_addresses and generic_method_instances accept addresses/states (generic_method_instances also method_indices), preserving every folded alternative; generic_method_issues accept rows. Row index is scoped to the metadata digest; native pages add binary_source. Uncollected native tables have null counts and remain unavailable. Desktop directory views use kind desktop-application and collection files/omissions/candidates/components/next_probes, with fields and pagination; files and omissions accept exact paths or their stable IDs, and candidates accept families. Source coverage and stable row IDs remain available. Native inventory Evidence from list_names, list/search_procedures or list/search_strings uses view: {kind: "native-inventory", offset: 0, limit: 64, fields: ["value"]}. Rows keep source_index and address even with selected fields. Summary counts describe retained rows; provider discovery coverage remains unknown. Native function Evidence accepts kind summary for identity, availability and facet counts, or kind native for selected facts: view: {kind: "native", facet: "procedure"}. Facets select procedure, pseudocode, assembly, basic blocks, comments, callers/callees, references, unresolved calls, referenced strings/names, native API and high-pcode. Use nonnegative offset and a positive caller-selected limit (defaults 0 and 64); pseudocode uses UTF-16 code units, other native pages use rows. Returns artifact identity, parent Evidence ID, a distinct view digest, coverage, limitations and unknowns. Actual serialized size determines MCP transport admission; choose a smaller page or export the complete retained Evidence if needed.

```json
{
    "type":  "object",
    "properties":  {
                       "source":  {
                                      "type":  "object"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "source",
                     "view"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_android_class

Inspect one exact Android class's parsed methods, fields and inner classes, including synthetic members. Returns display signatures, exact DEX prototypes and encoded static final field values without generating source. A prototype is not a complete raw method ID; overload indices remain consistent within the artifact and engine session.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "class_name":  {
                                          "type":  "string",
                                          "minLength":  1
                                      }
                   },
    "required":  [
                     "path",
                     "class_name"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_android_method

Decompile one Android method with smali fallback. Requires explicit overload_index when multiple methods share a name. Reports its DEX prototype, representation, fallback markers and source truncation; display signatures may be aliases.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "class_name":  {
                                          "type":  "string",
                                          "minLength":  1
                                      },
                       "method_name":  {
                                           "type":  "string",
                                           "minLength":  1
                                       },
                       "overload_index":  {
                                              "type":  "integer",
                                              "minimum":  0,
                                              "maximum":  9007199254740991
                                          }
                   },
    "required":  [
                     "path",
                     "class_name",
                     "method_name"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_android_package

Inspect a local APK's package, SDK and permission declarations, decoded manifest and class/resource counts, with a bounded static component projection. Omitted or complete detail preserves the complete result. Select detail: "summary" for all declarations, engine identity and counts without decoded manifest text or duplicate raw calls; retains the complete source and returns a distinct linked inspect_analysis_view summary. Read the exact retained manifest with view kind android, facet manifest, offset and limit (UTF-16 code units), using normalized_result.parent_evidence_id. Does not execute the APK or verify signatures. Requires a caller-supplied JADX MCP JAR and JDK; uses one worker in an isolated temporary workspace.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_apk_signature

Inspect declared v1/v2/v3/v3.1 APK signers, certificate fingerprints, algorithm IDs, content digests, SDK ranges, rotation lineage and source stamp without executing Android code. SHA-256 identifies the exact local file. Summary retains complete Evidence; an optional view selects signers/digests/lineage using stable IDs, scheme or signer-ID batches, fields and paging. Read retained pages with inspect_analysis_view kind apk-signature after the source is removed. IDs include artifact digest, scheme and source ordinal; certificates retain raw-DER SHA-256. Content-digest equality is not cryptographic signature verification: modified declarations can match modified bytes. Authenticity, signatures, lineage trust and v1 entry coverage remain unverified. Malformed structures and resource refusals are explicit; no device, network or SDK is used by REA.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Selected local APK/ZIP/AAB; static inspection only"
                                },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_apktool_client

Inspect the caller-selected apktool launcher: resolved command, selection source, and version. Decodes nothing. REA never installs Apktool or Java; select a launcher with REA_APKTOOL_COMMAND (absolute) or apktool on PATH.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_apple_ips

Inspect one explicit local Apple .ips crash report without executing a target, launching atos, or opening a disassembler. Parses the first-line metadata object and, when bug_type is the string 309, the crash body: process identity, exception and termination facts, threads, usedImages, lossless integer registers, and deterministic classification from documented SIGKILL, EXC_GUARD, pointer-authentication and dyld strings. Other bug_type values, including 288 stackshots, return no crash result. A missing or truncated body fails without partial success. Opened Mach-O and dSYM UUID joins remain unobserved. Portable on supported Node hosts; no Xcode or pwntools required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "minimum":  2,
                                              "maximum":  16777216
                                          },
                       "max_threads":  {
                                           "type":  "integer",
                                           "minimum":  1,
                                           "maximum":  4096
                                       },
                       "max_frames":  {
                                          "type":  "integer",
                                          "minimum":  1,
                                          "maximum":  65536
                                      },
                       "max_images":  {
                                          "type":  "integer",
                                          "minimum":  1,
                                          "maximum":  4096
                                      },
                       "max_registers":  {
                                             "type":  "integer",
                                             "minimum":  1,
                                             "maximum":  262144
                                         },
                       "max_integer_text_bytes":  {
                                                      "type":  "integer",
                                                      "minimum":  1,
                                                      "maximum":  67108864
                                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_artifact

Inspect an active archive or application package, including Node.js SEA carriers, in one call. By default returns the complete content-addressed artifact graph once, as the typed nested inventory Evidence, with root and integrity observations, integrity contradictions, hypotheses, unexplored branches, limitations, and next probes inline. For PCK and large containers prefer detail summary: it retains the complete inspection and returns its derived summary view; read exact paths, batch occurrence/content IDs and selected fields with inspect_analysis_view using parent_evidence_id. This changes delivery, not inventory coverage. Read-only DMG mounting is used automatically on supported macOS hosts. Integrity mismatches fail by default; record-and-continue keeps mismatches explicitly untrusted. It does not extract files; use extract_artifact to materialize regular contents of supported formats. DMG extraction is unavailable; use a caller-managed read-only mount on macOS to access its files.

```json
{
    "type":  "object",
    "properties":  {
                       "integrity_policy":  {
                                                "type":  "string",
                                                "enum":  [
                                                             "fail",
                                                             "record-and-continue"
                                                         ]
                                            },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_asset_catalog

Inspect compiled Assets.car metadata in an active Apple app bundle. Returns stable paginated catalog and rendition records with raw assetutil metadata and catalog digests; rendition bytes are not extracted.

```json
{
    "type":  "object",
    "properties":  {
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "limit":  {
                                     "type":  "integer",
                                     "minimum":  1,
                                     "maximum":  10000
                                 }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_binary_layout

Inspect an explicit local binary without launching it as a host process. Returns file-backed section/segment ranges, linked addresses, original symbol/REL/RELA table identities, complete encoded RELR tables and upstream-derived offsets with unknown per-word locations/addends, raw name bytes, dependency/interpreter names and static mitigation inferences inline with artifact SHA-256 Evidence. Profile: EXEC/DYN/REL ELF64 x86-64, ELF32 i386, ELF32 little-endian ARM and ELF32 little- or big-endian MIPS on Linux x64 via caller-supplied Python with unchanged pwntools 4.15.0, pyelftools 0.33 and Unicorn 2.1.2. Runtime addresses/library paths, overall relocation inventory completeness and complete derived GOT/PLT coverage remain unknown. Zero executable entry values mean absence; relocatable entries are not applicable. Upstream can emulate PLT instructions with Unicorn for derived static maps. Uses bounded complete output and owned temporary files/processes; malformed, unsupported or oversized objects return no partial success. Select detail summary to retain the complete layout Evidence in this session and receive only its summary view, then inspect sections, symbols, mitigations or linkage with inspect_analysis_view. pwntools_timeout_ms is each owned pwntools command's deadline (omitted means 30000 ms, range 1..2147483647, next call). cpuSeconds 30 and the output, diagnostic, and address-space byte allowances have no runtime override.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to the selected binary"
                                },
                       "pwntools_timeout_ms":  {
                                                   "type":  "integer",
                                                   "description":  "Deadline in milliseconds for each owned pwntools command in this call. Omitted means 30000. Integer from 1 to 2147483647. Applies on the next call. Does not raise cpuSeconds 30 or the output (67108864 bytes), diagnostic (1048576 bytes), or address-space (3221225472 bytes) allowances. Artifact admission stays REA_PWNTOOLS_MAX_INPUT_BYTES (default 33554432 bytes)."
                                               },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "complete returns the complete analysis Evidence inline. summary retains that complete Evidence in this session and returns its inspect_analysis_view summary; inspect pages, items or facets through normalized_result.parent_evidence_id."
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_byte_entropy

Inspect Shannon byte entropy for one explicit local file offline, without execution, extraction or external tools. Returns file-wide SHA-256 and entropy, fixed-block entropy for an optional offset/length window, and adjacent high (>=7.2) and low (<=1.0) runs with exact file offsets. Block size is a power of two from 256 to 65536 (default 4096). Complete output can become very large because it includes one row per selected block, and summary detail still retains that full list. Use a shorter length or a larger block_size; the selected window is limited to 32,768 output blocks. Entropy cannot distinguish compression from encryption. Summary detail returns file entropy plus the ten longest runs; retrieve parent Evidence for the full series. The entire file is read even for a window.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "block_size":  {
                                          "type":  "integer",
                                          "minimum":  256,
                                          "maximum":  65536
                                      },
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "length":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_dart_aot

Inspect one ABI's legacy split VM/isolate Dart AOT snapshot inside an APK (complete structural coverage: Dart 2.14 FullAOT kind 3). Unified _kDartSnapshotData/_kDartSnapshotText profiles are unsupported. Returns the four snapshot exports through the ELF dynamic symbol table with offsets, sizes and headers (magic, kind, length), snapshot hash, and a bounded string-pool projection from isolate data (package: source URIs, dart: SDK URIs, identifier-like tokens). String-pool entries are byte-pattern observations, not deserialized cluster semantics; typed class and function recovery needs an SDK-specific parser. Omitted or complete detail returns the full observation. Select detail: "summary" for target/ABI/library identity, all snapshot headers, coverage and original lower-bound counts without string lists. The complete source stays retained; read returned strings via inspect_analysis_view with parent_evidence_id and kind dart-aot, collection package_uris/dart_uris/class_like_tokens, offset and limit. Exhausting pages does not imply complete pool coverage. No engine, no execution.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "abi":  {
                                   "type":  "string",
                                   "minLength":  1,
                                   "pattern":  "^[^\\x2f]*$",
                                   "maxLength":  1010
                               },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_desktop_application

Inspect a local desktop application directory using bounded listings, file headers and selected metadata. Returns .NET, Unity Mono/IL2CPP, Unreal, Godot, GameMaker and Electron candidates, supporting paths/range hashes, component path markers and concrete next probes. No target execution, extraction, network or target changes. Default summary retains complete Evidence for inspect_analysis_view kind desktop-application; collections files/candidates/components/next_probes support paging and field selection, with exact file paths or candidate families. detail complete returns every retained fact inline. Snapshot identity covers examined metadata/read ranges, not all file contents; coverage and unavailable probes remain explicit.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Local filesystem path used by this operation."
                                },
                       "max_entries":  {
                                           "type":  "integer",
                                           "description":  "Maximum permitted entries for this operation."
                                       },
                       "max_probe_bytes":  {
                                               "type":  "integer",
                                               "description":  "Maximum permitted probe bytes for this operation."
                                           },
                       "detail":  {
                                      "type":  "string"
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_devicetree

Inspect an explicit local flattened devicetree blob (FDT v16/v17) without execution or external tools. Returns validated header, memory reservations, full node/property tree with bounded raw hex, derived standard strings, cells and parent-sized reg tuples, aliases and selected single-cell phandle references with unresolved targets. Binding-specific arrays, overlays and runtime hardware remain unknown. Malformed structure or exhausted named budgets fails without partial success. Complete detail returns all Evidence inline; summary retains it for inspect_analysis_view kind devicetree, collections nodes, properties (requires exact path), reservations and references, with offset/limit pagination.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "minimum":  40,
                                              "maximum":  16777216
                                          },
                       "max_nodes":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  65536
                                     },
                       "max_properties":  {
                                              "type":  "integer",
                                              "minimum":  1,
                                              "maximum":  262144
                                          },
                       "max_depth":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  256
                                     },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_electron_fuses

Inspect packaged Electron fuse bytes in one local macOS app bundle, Electron Framework binary, Windows PE executable, or Linux ELF executable. Returns the artifact digest, per-slice wire version, fuse index, version-1 name, state, and raw byte, plus derived implications for Inspector flags, ELECTRON_RUN_AS_NODE, embedded ASAR integrity, and app.asar-only loading. A missing sentinel is unknown fuse state. Reads artifact bytes only and does not modify the selected file or open a runtime target.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a macOS .app bundle, Electron Framework binary, Windows PE executable, or Linux ELF executable"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_electron_page

Passively inspect one Electron file page by supplying its loopback CDP endpoint and target ID. The provider rechecks that the target currently exists at that endpoint and is a local file page. Returns frames, DOM structure, resource paths, and scripts without evaluating renderer JavaScript or invoking Electron APIs. The selected endpoint exposes every local file page and its metadata to this tool.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "observation_ms":  {
                                              "type":  "integer",
                                              "minimum":  0,
                                              "maximum":  9007199254740991
                                          },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              },
                       "include_script_sources":  {
                                                      "type":  "boolean"
                                                  }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Provide a complete input group: cdp_endpoint + target_id + [observation_ms] + [command_timeout_ms] + [include_script_sources]; cdp_endpoint + target_id + include_script_sources + [observation_ms] + [command_timeout_ms].",
    "minProperties":  2,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_elf_notes

Inspect one explicit local ELF32/ELF64 image's PT_NOTE program headers and SHT_NOTE sections without executing it or opening a disassembler. Returns note-table file ranges, owners, types, descriptor SHA-256 and hex previews, plus GNU build-id, ABI-tag and gold-version fields when those descriptors match. A missing table is observed absence of those declarations, not proof the image never carried notes in another container. Reads a stable regular file; malformed ELF headers, truncated notes and exhausted budgets return no partial success. Portable on supported Node hosts; no debug package or catalog is required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "minimum":  64,
                                              "maximum":  536870912
                                          },
                       "max_entries":  {
                                           "type":  "integer",
                                           "minimum":  1,
                                           "maximum":  256
                                       }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_erlang_beam

Inspect one explicit local Erlang BEAM IFF container without executing bytecode or installing Erlang. Returns form/chunk byte ranges and payload SHA-256, atoms, export/import/lambda tables with resolved atom names, and the Code header. Complete output is the default. detail summary retains complete Evidence and returns module/header/counts; include a beam view for selected atoms/exports/imports/lambdas/chunks in the first call, then page retained or offline facts with inspect_analysis_view. Exact names/indices/fields select facts; import modules intersects function names. Same-name arities survive under parent-and-collection-scoped source_index; atom and lambda indexes retain their original meaning. Missing tables stay null in counts; unresolved names stay null and runtime absence remains unknown. Reads a stable regular file; malformed/truncated input and exhausted parser budgets return no partial success. Portable on supported Node hosts; no BEAM emulator required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a BEAM file"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "description":  "Maximum stable artifact bytes to read (default 64 MiB)"
                                          },
                       "max_chunks":  {
                                          "type":  "integer",
                                          "description":  "Maximum IFF chunks to examine; exceeding the budget fails without partial success"
                                      },
                       "max_atoms":  {
                                         "type":  "integer",
                                         "description":  "Maximum atom-table entries to decode; exceeding the budget fails without partial success"
                                     },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "minProperties":  1,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_evm_interface

Inspect explicit local flat EVM bytecode without target execution or chain/RPC access. Select raw or hex encoding explicitly. Omitted or complete detail returns original carrier and decoded identities, full bytecode and all inferred interface candidates inline. For all inferred selectors/offsets/arguments/mutability and identities without bytecode hex or duplicated stdout, select detail: "summary" on this first call: it retains unchanged complete Evidence and returns a distinct linked inspect_analysis_view summary. Read original function rows or bytecode_hex/stdout/stderr text pages using view kind evm and normalized_result.parent_evidence_id; original complete Evidence remains accessible. Bytecode kind, hardfork, deployed authenticity and discovery completeness stay unknown; summary coverage covers only producer-returned candidates. Unchanged bundled EVMole 0.9.3/WASM in an owned bounded worker is initially real-verified on Linux x64; EOF-style EF00 containers are unsupported. Adjust timeout_ms (default 30000 ms, integer 1..2147483647) for the owned worker command on the next call; omitted timeout_ms keeps 30000 ms. Fixed byte, address-space, and CPU allowances stay unchanged. No signature lookup, wallet action or transaction broadcast.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute path to caller-selected local bytecode carrier"
                                },
                       "encoding":  {
                                        "type":  "string",
                                        "description":  "Raw bytes or UTF-8 hex with optional 0x prefix and outer ASCII whitespace"
                                    },
                       "timeout_ms":  {
                                          "type":  "integer",
                                          "description":  "Deadline in milliseconds for the owned EVMole worker command (default 30000), not carrier acquisition or final reply reads. An integer from 1 to 2147483647 applies on the next call. Fixed input, reply, diagnostic, address-space, and CPU allowances stay unchanged."
                                      },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "complete returns the complete analysis Evidence inline. summary retains that complete Evidence in this session and returns its inspect_analysis_view summary; inspect pages, items or facets through normalized_result.parent_evidence_id."
                                  }
                   },
    "required":  [
                     "path",
                     "encoding"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_firmware_regions

Inspect signature candidates in a local firmware file with Binwalk 3.1.x (verified with 3.1.0). Returns offsets, reported lengths, descriptions and raw confidence values. Length validation and runtime addresses remain unknown. No extraction or target execution. Linux, caller-supplied tool, util-linux prlimit and procps ps required; 128 MiB input limit, one worker. Adjust analysis_timeout_ms for the analysis wall-clock budget (default 120000); fixed per-process limits are unchanged.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local firmware file; never executed. Relative paths are rejected."
                                },
                       "analysis_timeout_ms":  {
                                                   "type":  "integer",
                                                   "description":  "Analysis wall-clock budget in milliseconds (default 120000), from tool resolution through inspection and its final workspace cleanup, or extraction through inventory. Excludes queue admission and extraction publication. Does not raise fixed CPU, memory or byte limits."
                                               }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_go_binary

Inspect embedded Go compiler version, main module, dependency/replacement versions and build settings in an explicit local ELF, PE or thin Mach-O image, or the slice of a universal Mach-O named by architecture (read without lipo; artifact stays the universal file, selected_slice records the slice and source ranges stay file offsets). Default detail complete returns original artifact SHA-256, exact build-info source ranges, raw framed module bytes and unparsed module-text lines inline as observed Evidence. Explicit detail summary retains the same complete parent and returns compiler/main-module identity, counts, source ranges and parent_evidence_id. Query retained rows through inspect_analysis_view kind go, collection dependencies or settings; select exact dependency paths or setting keys, indices, fields and pagination. Rows always retain their original index scoped to the parent Evidence; empty filters select none. Runtime dependency completeness remains unknown. Supports inline and historical pointer encodings without installing Go, opening a disassembler, executing the target, writing temporary files or accessing the network. Missing metadata is reported as unknown, not proof that an image is not Go. Does not recover functions, source mappings, types or runtime behavior. Resource guards bound files to 256 MiB and charge each distinct build-info search span in full against a cumulative 256 MiB header-search budget, with partial overlaps charged separately; they also bound aggregate embedded strings to 1 MiB, structural table decoding to 16 MiB and complete inline metadata to 8 MiB; malformed, unsupported and oversized inputs receive typed failures rather than partial success.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to the selected executable or library"
                                },
                       "architecture":  {
                                            "type":  "string",
                                            "description":  "CPU family of the slice to read from a universal (FAT) Mach-O. Required for universal files and rejected for every other image."
                                        },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "complete returns all metadata inline; summary retains complete Evidence and returns build identity and counts for subsequent retained Go views."
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_gpu_code_objects

Inspect static CUDA/HIP GPU code objects in local ELF/PE hosts, CUDA fatbins, CUBIN, PTX and Clang HIP bundles (CUDA LZ4/Zstd and CCOB v1/v2/v3 zlib/Zstd). Default inventory reads directories; full-file SHA-256 always streams every byte. analysis kernels decodes only selected object_ids/targets (object_ids require expected_sha256 from their inventory), retaining all inventory rows and explicit partial/unknown states. Reports PTX virtual registers, CUBIN physical registers/nv.info parameters/shared memory and AMDGPU MessagePack kernel declarations separately. No GPU tools, target execution, network, SASS disassembly or host launch correlation. Unsupported CUDA compression retains encoded locations. Default summary retains complete Evidence; optional view returns a selected targets/objects/kernels page or PTX text page in this call. inspect_analysis_view kind gpu-code-objects reuses retained facts without reopening files; PTX text uses UTF-16 code-unit pages. Object IDs are positional within artifact.sha256. One active inspection; 128 MiB/object, 512 MiB decoded work, 16 MiB retained facts. Full Evidence includes selected PTX text artifacts with SHA-256 and encoded/decoded provenance.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Local filesystem path used by this operation."
                                },
                       "analysis":  {
                                        "type":  "string"
                                    },
                       "expected_sha256":  {
                                               "type":  "string",
                                               "description":  "Require the current full-file digest; mandatory when selecting inventory object_ids"
                                           },
                       "object_ids":  {
                                          "type":  "array",
                                          "items":  {
                                                        "type":  [
                                                                     "null",
                                                                     "boolean",
                                                                     "object",
                                                                     "array",
                                                                     "number",
                                                                     "string"
                                                                 ],
                                                        "items":  {

                                                                  }
                                                    },
                                          "description":  "Decode only these inventory IDs; IDs are scoped to the artifact SHA-256"
                                      },
                       "targets":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 },
                                       "description":  "Decode only exact target declarations, preserving all inventory rows"
                                   },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_il2cpp_metadata

Inspect local Unity IL2CPP metadata (24/27/29/31/39), optionally linking a caller-selected 64-bit little-endian Mach-O, PE or ELF binary. Static reading only. Default summary retains complete Evidence; use parent_evidence_id with inspect_analysis_view kind il2cpp-metadata for named collections, indices, fields and paging. Parameters accept method_indices; method_addresses accept addresses and states. Stable row indices are scoped to the metadata digest; native pages also identify the binary. Native states distinguish executable code, observed zero slots (no-body), and unknown owners/relocations. Shared addresses preserve all methods. code_gen_modules pages accept image_indices; summaries omit that table. Type names are C#-like; version27 retains indices with unknown names. generic_instances adds genericMethodTable rows with Il2CppDumper-style names (versions 27/29/31, static CodeRegistration and MetadataRegistration); generic_method_instances pages accept method_indices, addresses and states; summaries count issues, paged as generic_method_issues. Unrequested native counts are null. Static-table links are derived; the x86-64/arm64 executable-byte fallback is inferred and does not verify instruction boundaries. Input guards:256MiB metadata/1GiB binary;64MiB encoded report; one active inspection. Unsupported layouts, pointer encodings and resource limits have typed reasons. Full Evidence remains available with detail complete. Neither static linking nor structural agreement proves runtime behavior or a same-build pair.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a Unity IL2CPP global-metadata.dat file"
                                },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "summary (default) retains complete Evidence and returns identity and collection counts; complete returns every image, type, method, member, parameter and string-literal row inline"
                                  },
                       "binary_path":  {
                                           "type":  "string",
                                           "description":  "Absolute path to the caller-selected matching 64-bit IL2CPP binary (Mach-O GameAssembly.dylib, PE GameAssembly.dll or ELF libil2cpp.so); static reading, never executed"
                                       },
                       "binary_architecture":  {
                                                   "type":  "string",
                                                   "description":  "Slice to read from a universal Mach-O binary_path; required for universal binaries and rejected otherwise"
                                               },
                       "generic_instances":  {
                                                 "type":  "boolean",
                                                 "description":  "Also link generic method instances (genericMethodTable rows) to code addresses with Il2CppDumper-style Type\u003cArgs\u003e$$Method\u003cArgs\u003e names; needs binary_path"
                                             }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_jeb_client

Inspect the running JEB client serving MCP: engine version, GUI or headless mode, and startup time. Does not open a target. Requires a caller-started JEB client reachable at REA_JEB_MCP_URL (default http://127.0.0.1:8425/mcp); REA never installs or launches JEB.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_kernel_module

Inspect one explicit local ELF32/ELF64 ET_REL Linux kernel module without executing it or opening a disassembler. Returns ordered .modinfo key/value entries (duplicates preserved), __versions symbol CRCs, presence of .gnu.linkonce.this_module and __ksymtab sections, observed .init.text and .exit.text sizes, and an appended module signature trailer with its module_signature fields plus a PKCS#7 byte range and SHA-256. Does not execute the module, decode struct module, or validate certificates. A missing section is observed absence when section names are known, not proof the module never carried that metadata. Other ELF types are not applicable. gzip, xz, and zstd containers are unsupported and name external_decompression as the next probe; neither this tool nor extract_artifact decodes those codecs, so decompress with an external utility and inspect the result. Reads a stable regular file; malformed ELF headers and exhausted entry budgets return no partial success. Portable on supported Node hosts.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "minimum":  64,
                                              "maximum":  536870912
                                          },
                       "max_entries":  {
                                           "type":  "integer",
                                           "minimum":  1,
                                           "maximum":  65536
                                       }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_keyed_archive

Inspect one Foundation NSKeyedArchiver plist in the active app bundle as original object-table nodes, class descriptors, named roots, and UID edges. Preserves shared and cyclic references without instantiating classes. The path is relative to the bundle; root selection and object pagination preserve original identities.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "root":  {
                                    "type":  "string"
                                },
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "limit":  {
                                     "type":  "integer",
                                     "minimum":  1,
                                     "maximum":  20000
                                 }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_macho

Inspect Mach-O slices, load commands, imports, exports, dependencies, build metadata, segments, sections, permissions, and exact command provenance without launching Hopper. Large frameworks return megabytes of symbols; detail summary retains the complete Evidence and returns slice identity with per-collection counts, then page rows with inspect_analysis_view kind macho. native_command_timeout_ms is each Xcode command's wall clock (omitted means 60000 ms, range 1..2147483647, next call) and is distinct from objdump/rizin command_timeout_ms. It does not raise the fixed 67108864-byte output allowance.

```json
{
    "type":  "object",
    "properties":  {
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  },
                       "native_command_timeout_ms":  {
                                                         "type":  "integer",
                                                         "minimum":  1,
                                                         "maximum":  2147483647
                                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_managed_artifact

Inspect an explicit PE/CLI path, or the selected managed target, without loading or executing target code. Omitted or complete detail returns complete metadata inventories with original coverage. Select detail: "summary" on the first call for complete identity, PE/CLI, classification with its evidence, module/MVID, assembly, frameworks, returned-collection counts, coverage/issues and limitations. It retains complete Evidence and returns a distinct linked inspect_analysis_view summary. Page original references/resources/attributes with view kind managed and normalized_result.parent_evidence_id. Counts cover producer-returned rows; partial, unavailable or malformed metadata remains explicit and tokens remain build-local.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "allOf":  [
                                                  {
                                                      "pattern":  "^[^\\x00]*$"
                                                  },
                                                  {
                                                      "pattern":  "^\\/"
                                                  }
                                              ]
                                },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_managed_members

Inspect PE/CLI metadata members, signatures, raw CIL hashes, decoded-instruction-tuple hashes, separately reported exception regions, call edges, field-access anchors, and the #US string literals that ldstr loads, without loading or executing target code. Select type_names to decode only the members of those exact declaring types and the literals their bodies load, which keeps large assemblies such as a base class library within the representation budget. Metadata tokens are reported as build-local coordinates bound to the artifact SHA-256 and MVID; the tuple hash does not resolve them or fully commit control flow. Omitted or complete detail returns every collection, including the assembly-wide member_refs and exported_types, which type_names does not narrow. Select detail: "summary" for identity, metadata status, selection with unmatched names, coverage/issues, limitations and per-collection counts; it retains complete Evidence and returns a linked inspect_analysis_view summary. Page original rows with view kind managed-members, normalized_result.parent_evidence_id and collection types/exported_types/fields/methods/member_refs/call_edges/field_accesses/user_strings.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "allOf":  [
                                                  {
                                                      "pattern":  "^[^\\x00]*$"
                                                  },
                                                  {
                                                      "pattern":  "^\\/"
                                                  }
                                              ]
                                },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  },
                       "type_names":  {
                                          "minItems":  1,
                                          "type":  "array",
                                          "items":  {
                                                        "type":  "string",
                                                        "minLength":  1
                                                    },
                                          "uniqueItems":  true
                                      }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_managed_native_boundaries

Inspect PE/CLI ModuleRef, ImplMap/PInvoke declarations, CLI native-header indicators, and non-IL method implementation flags without loading or executing target code. Results are managed declarations and degraded native-boundary observations, not verified native exports or addresses.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "allOf":  [
                                                  {
                                                      "pattern":  "^[^\\x00]*$"
                                                  },
                                                  {
                                                      "pattern":  "^\\/"
                                                  }
                                              ]
                                }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_model_artifact

Inspect local GGUF v2/v3 or safetensors declarations by content, without loading models or executing code. Default hash header reads only the header: full-file SHA-256 and Evidence subject are unknown; artifact.header_sha256 is a scoped prefix commitment, not weight identity. hash full additionally streams all file bytes for SHA-256. Optional view returns a selected metadata/tensor/type page plus model context in this call. Summary retains complete Evidence for inspect_analysis_view kind model-artifact, collection metadata (keys/indices/fields) or tensors (names/types/indices/fields), with stable header-scoped indices and paging. Metadata arrays retain eight-element typed previews; scalar metadata is complete. Tensor counts, shapes, numeric type IDs and offsets are observed; textual types and parameter/storage totals use established format facts. Unknown or historically ambiguous GGUF types retain IDs with unknown nonempty byte lengths; ambiguous identities use AMBIGUOUS_<id> labels. Unresolved Q8_1 and K-block layouts also keep nonempty byte lengths unknown. Tensor payload, model behavior and inference quality are not inspected. One active inspection, 100 MB header ceiling and 32 MiB retained-facts budget; resource refusals are explicit. No network, external files, Python, model runtime, extraction or deserialization of pickle.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Local filesystem path used by this operation."
                                },
                       "hash":  {
                                    "type":  "string",
                                    "description":  "header reads declarations only; full streams all bytes for SHA-256"
                                },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object",
                                    "description":  "Return this selected page with compact model context, retaining complete Evidence; requires summary detail"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_native_api

Analyze a native API boundary in one function identified by symbol or provider-returned address. Returns confidence, evidence, jump-table dispatch/data/case mappings, separately evidenced default targets, unsupported branches, and residual unknowns. Null case values mean unresolved cases, never a known default.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## inspect_native_data_type

Inspect one recovered type by exact database category path or defined typed data address. Returns observed size, alignment, packing, fields, bitfields, enum values, and child type identities; source-level authority and flexible-tail semantics remain unknown unless substantiated.

```json
{
    "type":  "object",
    "properties":  {
                       "type":  {
                                    "type":  "string",
                                    "minLength":  1
                                },
                       "address":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_native_dispatch_metadata

Inspect the bound provider's name inventory for Objective-C class/method symbols and Swift mangled symbols, returning at most max_records decoded names. Optional objc_class_import_addresses request exact pointer-slot binding facts for those addresses; they are decoded only from the selected Mach-O slice and retain partial or unsupported coverage when not established. Includes target SHA-256 when available, provider identity/version, analysis-profile digest, facet coverage, and per-record Evidence. This is static symbol and metadata evidence, not runtime metadata: ivar layouts, protocol conformances, witness/class tables, and relative pointers are explicitly marked unsupported unless a provider supplies those decoders. The provider controls how its name inventory is acquired. The Mach-O byte reader holds the whole file in memory: max_input_bytes (default 64 MiB, at most 1 GiB) bounds it, and results record input_admission.

```json
{
    "type":  "object",
    "properties":  {
                       "max_records":  {
                                           "type":  "integer",
                                           "minimum":  1,
                                           "maximum":  20000
                                       },
                       "objc_class_import_addresses":  {
                                                           "maxItems":  20000,
                                                           "type":  "array",
                                                           "items":  {
                                                                         "type":  "string"
                                                                     }
                                                       },
                       "objc_class_import_addresses_complete":  {
                                                                    "type":  "boolean"
                                                                },
                       "max_input_bytes":  {
                                               "type":  "integer",
                                               "minimum":  1,
                                               "maximum":  1073741824
                                           }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_native_exports

Inspect one explicit local PE or ELF image's export table without executing it or opening an engine: PE export-directory names, ordinals, addresses and forwarders, or ELF defined default/protected global, weak and unique .dynsym symbols with type, binding and symbol version. Ordinal-only, forwarded and hidden-version exports stay distinct. Portable on any supported host. Coverage is partial when table entries are unreadable and unsupported when ELF dynamic symbols cannot be enumerated from the available section metadata; an empty complete inventory means the image declares no exports. Mach-O exports use inspect_macho. Omitted/complete detail preserves inline delivery. Select detail summary to retain complete Evidence and return identity, counts and source coverage. Query inspect_analysis_view kind native-exports, collection exports or issues, with exact names/original indices, fields and matching-row pagination. Names return every version or alias; source_index is scoped to the parent Evidence. ELF name projections keep raw-byte and encoding qualifications.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a PE or ELF image"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "description":  "Maximum stable artifact bytes to read (default 256 MiB)"
                                          },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "complete returns the complete analysis Evidence inline. summary retains that complete Evidence in this session and returns its inspect_analysis_view summary; inspect pages, items or facets through normalized_result.parent_evidence_id."
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_native_instruction

Inspect one exact code or data address: decoded bytes, mnemonic, ordered operand tokens, flow, outgoing instruction references and typed incoming references where available. Incoming references preserve source, access kind, operand index and analysis provenance even at data addresses; they do not prove runtime access. Memory addressing decomposition is unavailable when the provider supplies only tokens. Data, interior instruction addresses and undecodable bytes are distinct outcomes.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_native_load_image

Verify the provider's loaded DOS MZ or explicitly selected COM image against its immutable target snapshot: complete original/modified source-byte digests, header/module file mappings and memory digests, relocations and external entry. Returns measured observations and independent checks with mismatch coordinates. COM verification includes its imposed entry register context and does not claim PSP/runtime coverage. Overlay and uninitialized coverage remain explicit; other formats return unsupported. Does not execute or modify the target.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_packet_capture

Inspect caller-selected fields in an existing local packet capture with TShark 4.x. Return 50 matching frames by default, with match_offset/match_limit paging and coverage counts; match_limit:null requests all remaining matches. Preserve repeated values, frame numbers, display filter, decode-as rules and pass mode in inline Evidence with source/tool identity and diagnostics. Optional packet_limit counts input packets before filtering; output paging does not shorten dissection or two-pass reassembly. Requires Linux, util-linux prlimit, and TShark 4.x on PATH or through absolute REA_TSHARK_COMMAND. File-only analysis; no live capture or device communication. Missing fields or no matches do not establish absence of device traffic.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local capture path"
                                },
                       "fields":  {
                                      "type":  "array",
                                      "items":  {
                                                    "type":  [
                                                                 "null",
                                                                 "boolean",
                                                                 "object",
                                                                 "array",
                                                                 "number",
                                                                 "string"
                                                             ],
                                                    "items":  {

                                                              }
                                                },
                                      "description":  "TShark field names to return, e.g. ip.addr, usb.endpoint_address, btatt.opcode; frame.number is always included"
                                  },
                       "display_filter":  {
                                              "type":  [
                                                           "string",
                                                           "null"
                                                       ],
                                              "description":  "Optional Wireshark display filter (-Y), applied within the read limit"
                                          },
                       "decode_as":  {
                                         "type":  "array",
                                         "items":  {
                                                       "type":  [
                                                                    "null",
                                                                    "boolean",
                                                                    "object",
                                                                    "array",
                                                                    "number",
                                                                    "string"
                                                                ],
                                                       "items":  {

                                                                 }
                                                   },
                                         "description":  "Explicit TShark decode-as selections, e.g. tcp.port==8888,http"
                                     },
                       "packet_limit":  {
                                            "type":  [
                                                         "integer",
                                                         "null"
                                                     ],
                                            "description":  "Maximum input packets to read before filtering; omitted scans to EOF within resource limits"
                                        },
                       "two_pass":  {
                                        "type":  "boolean",
                                        "description":  "Use two-pass analysis for fields requiring later packets; false requests a single pass"
                                    },
                       "match_offset":  {
                                            "type":  "integer",
                                            "description":  "Skip this many matching packets after filtering; default 0"
                                        },
                       "match_limit":  {
                                           "type":  [
                                                        "integer",
                                                        "null"
                                                    ],
                                           "description":  "Return at most this many matching packets after filtering; default 50. Null explicitly returns all remaining matches within producer resource limits."
                                       }
                   },
    "required":  [
                     "path",
                     "fields"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_packet_fields

Discover protocols or field names/types registered in the installed TShark 4.x. kind defaults to protocols; use fields and optional exact protocol abbreviation to select field definitions. query searches name/abbreviation/description by case-insensitive literal substring using Unicode simple case folding. Return 50 matches by default with offset/limit paging; null requests all remaining within resource limits. Preserve type/base/bitmask/description, duplicate registrations, source line numbers, coverage, tool identity and diagnostics. Linux, TShark 4.x (PATH or absolute REA_TSHARK_COMMAND) and prlimit required. No capture file, interface or binary target needed. Registrations describe tool capabilities, not observed traffic. Each call rereads the glossary; use selected fields with inspect_packet_capture.

```json
{
    "type":  "object",
    "properties":  {
                       "kind":  {
                                    "type":  "string",
                                    "enum":  [
                                                 "protocols",
                                                 "fields"
                                             ]
                                },
                       "protocol":  {
                                        "anyOf":  [
                                                      {
                                                          "type":  "string",
                                                          "minLength":  1,
                                                          "maxLength":  8192
                                                      },
                                                      {
                                                          "type":  "null"
                                                      }
                                                  ]
                                    },
                       "query":  {
                                     "type":  "string",
                                     "maxLength":  8192
                                 },
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "limit":  {
                                     "anyOf":  [
                                                   {
                                                       "type":  "integer",
                                                       "exclusiveMinimum":  0,
                                                       "maximum":  9007199254740991
                                                   },
                                                   {
                                                       "type":  "null"
                                                   }
                                               ]
                                 }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_pcap_capture

Request detail summary for capture identity/counts, then page retained facts with inspect_analysis_view kind pcap. Default detail complete preserves inline delivery. Inspect one saved pcap or pcapng capture through an owned snapshot: metadata, exact packet count, SHA-256, IPv4/IPv6 address counts, DNS questions and address values from the same single-question response, TLS SNI, protocol distribution and HTTP URIs. All derived inventories are retained within declared resource budgets; unknown DNS attribution and excluded credentials remain explicit. Linux requires util-linux prlimit; Windows uses owned Job Objects. Requires caller-supplied capinfos and tshark; REA never captures traffic.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_pcap_client

Inspect the caller-selected tshark and capinfos binaries: selected commands, selection sources, and versions. Both tools must be usable. Reads no capture file. REA never installs the Wireshark suite; select binaries with REA_TSHARK_COMMAND and REA_CAPINFOS_COMMAND (absolute) or from PATH.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_pe_certificates

Inspect one explicit local PE32/PE32+ image's IMAGE_DIRECTORY_ENTRY_SECURITY table without executing it, verifying signatures or opening a disassembler. Returns WIN_CERTIFICATE file ranges, types and payload SHA-256, plus structurally decoded PKCS#7 signedData digest algorithms, embedded X.509 serial/issuer/subject/validity fields and signer identifiers, including a subject key identifier, when type 2 is present. A missing directory is observed absence of that table, not proof the image is unsigned in every catalog or overlay sense. Authenticode digest coverage, chain validity and timestamp trust remain unobserved. Reads a stable regular file; malformed PE headers, truncated WIN_CERTIFICATE entries, X.509 certificates missing subjectPublicKeyInfo or signatureValue, and non-signedData PKCS#7 return no partial success. Portable on supported Node hosts; no Windows SDK or engine installation required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a PE image"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "description":  "Maximum stable artifact bytes to read (default 64 MiB)"
                                          },
                       "max_entries":  {
                                           "type":  "integer",
                                           "description":  "Maximum WIN_CERTIFICATE entries to examine; exceeding the budget fails without partial success"
                                       }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_pe_imports

Inspect one explicit local PE32/PE32+ image's IMAGE_DIRECTORY_ENTRY_IMPORT and delay-load tables without executing it or opening a disassembler. Returns DLL names, named and ordinal functions, ILT/IAT RVAs, delay-load descriptors, and the pefile import hash of the standard import table, withheld with a reason when that table is partial or pefile would hash a different row sequence. A missing import directory is observed absence of that table, not proof the image never imported APIs through another table or overlay. Bound snapshots, loaded IAT contents and export-forward resolution remain unobserved. Reads a stable regular file; malformed PE headers and exhausted DLL or function budgets return no partial success. Unreadable names or thunks and descriptors missing a required table are reported as partial coverage with issues. Portable on supported Node hosts; no Windows SDK or engine installation required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a PE image"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "description":  "Maximum stable artifact bytes to read (default 64 MiB)"
                                          },
                       "max_dlls":  {
                                        "type":  "integer",
                                        "description":  "Maximum import and delay-load descriptors to examine; exceeding the budget fails without partial success"
                                    },
                       "max_functions":  {
                                             "type":  "integer",
                                             "description":  "Maximum imported functions to examine across every table; exceeding the budget fails without partial success"
                                         }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_pe_load_config

Inspect PE32/PE32+ TLS callbacks and sized load configuration without executing the image. Returns callback VA/RVA/file offsets and sections, security cookie, SafeSEH (PE32), CFG flags and table pointers/counts, EH continuation and XFG fields. Flag-derived mitigation claims are labeled inferred. Absent directories are null; malformed structures and exhausted budgets fail without partial success. Reads a stable local regular file without a disassembler.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a PE image"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "description":  "Maximum stable artifact bytes to read (default 64 MiB)"
                                          },
                       "max_callbacks":  {
                                             "type":  "integer",
                                             "description":  "Maximum non-null TLS callbacks to examine; exceeding the budget fails without partial success"
                                         }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_pe_resources

Inspect one explicit local PE32/PE32+ image's complete bounded type/name/language resource tree without executing it or opening a disassembler. Returns numeric IDs versus original UTF-16 names, directory identity paths including empty branches, directory/data-entry file locations, payload RVA/file ranges, complete SHA-256 hashes and static RT_GROUP_ICON image-resource candidates. Language fallback, image decoding and actual shell/window loading remain unobserved. Reads a stable regular file; malformed or ambiguous mappings, non-identical overlapping ranges and exhausted budgets return no partial success. Portable on supported Node hosts; no Windows SDK or engine installation required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a PE image"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "description":  "Maximum stable artifact bytes to read (default 64 MiB)"
                                          },
                       "max_entries":  {
                                           "type":  "integer",
                                           "description":  "Maximum resource directory entries to examine; exceeding the budget fails without partial success"
                                       }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_plist

Requires an open binary or bundle-resource session; use open_binary or open_bundle_resources first. Parse its Info.plist by default, or select a local plist with path within that session. Supplying path does not open a session. Returns normalized JSON rather than plutil text. native_command_timeout_ms is each file or plutil command's wall clock (omitted means 60000 ms). The 67108864-byte plist input and decode allowances and the 998-level nesting allowance have no runtime override. XML sources also require native plutil validation, which can refuse shallower inputs with its own diagnostics. The 4096-byte markup probe is a probe window, not a document cap, and has no runtime override.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "native_command_timeout_ms":  {
                                                         "type":  "integer",
                                                         "minimum":  1,
                                                         "maximum":  2147483647
                                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_python_pyc

Inspect one explicit local CPython 3.7–3.13 .pyc file with a supported 16-byte header without executing bytecode, invoking CPython or recovering source. Returns the header (magic, pinned python_version, PEP 552 flags, source hash or mtime/size), the top-level code object with nested functions, names, const summaries, and bytecode length/SHA-256. Complete output is the default. detail summary retains the complete Evidence and returns header/coverage. Include an optional python-pyc code_objects view to return selected names/indices/fields in the first call; inspect_analysis_view pages the same retained source later. Every matching display name survives under its parent-scoped source_index; nested_code represents code-constant references, not runtime calls. Marshal is walked with object, depth and string budgets; unsupported magics/layouts, truncated streams and exhausted budgets return no partial success. A decoded header is not proof the matching .py is present. Portable on supported Node hosts; no Python installation required.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "max_file_bytes":  {
                                              "type":  "integer",
                                              "minimum":  16,
                                              "maximum":  67108864
                                          },
                       "max_objects":  {
                                           "type":  "integer",
                                           "minimum":  1,
                                           "maximum":  65536
                                       },
                       "max_depth":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  256
                                     },
                       "max_string_bytes":  {
                                                "type":  "integer",
                                                "minimum":  1,
                                                "maximum":  8388608
                                            },
                       "detail":  {
                                      "anyOf":  [
                                                    {
                                                        "type":  "string",
                                                        "const":  "complete"
                                                    },
                                                    {
                                                        "type":  "string",
                                                        "const":  "summary"
                                                    }
                                                ]
                                  },
                       "view":  {
                                    "type":  "object",
                                    "properties":  {
                                                       "kind":  {
                                                                    "type":  "string",
                                                                    "const":  "python-pyc"
                                                                },
                                                       "collection":  {
                                                                          "type":  "string",
                                                                          "const":  "code_objects"
                                                                      },
                                                       "names":  {
                                                                     "type":  "array",
                                                                     "items":  {
                                                                                   "type":  "string"
                                                                               }
                                                                 },
                                                       "indices":  {
                                                                       "type":  "array",
                                                                       "items":  {
                                                                                     "type":  "integer",
                                                                                     "minimum":  0,
                                                                                     "maximum":  9007199254740991
                                                                                 }
                                                                   },
                                                       "fields":  {
                                                                      "type":  "array",
                                                                      "items":  {
                                                                                    "type":  "string",
                                                                                    "enum":  [
                                                                                                 "name",
                                                                                                 "qualname",
                                                                                                 "filename",
                                                                                                 "firstlineno",
                                                                                                 "argcount",
                                                                                                 "posonlyargcount",
                                                                                                 "kwonlyargcount",
                                                                                                 "nlocals",
                                                                                                 "stacksize",
                                                                                                 "flags",
                                                                                                 "names",
                                                                                                 "varnames",
                                                                                                 "freevars",
                                                                                                 "cellvars",
                                                                                                 "localsplusnames",
                                                                                                 "localspluskinds_hex",
                                                                                                 "bytecode",
                                                                                                 "consts",
                                                                                                 "nested_code"
                                                                                             ]
                                                                                }
                                                                  },
                                                       "offset":  {
                                                                      "type":  "integer",
                                                                      "minimum":  0,
                                                                      "maximum":  9007199254740991
                                                                  },
                                                       "limit":  {
                                                                     "type":  "integer",
                                                                     "exclusiveMinimum":  0,
                                                                     "maximum":  9007199254740991
                                                                 }
                                                   },
                                    "required":  [
                                                     "kind",
                                                     "collection"
                                                 ],
                                    "additionalProperties":  false
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "minProperties":  1,
    "additionalProperties":  false,
    "if":  {
               "properties":  {
                                  "detail":  {
                                                 "const":  "summary"
                                             }
                              },
               "required":  [
                                "detail"
                            ]
           },
    "then":  {
                 "required":  [
                                  "detail"
                              ]
             },
    "else":  {
                 "properties":  {
                                    "view":  {
                                                 "not":  {
                                                             "type":  [
                                                                          "null",
                                                                          "boolean",
                                                                          "object",
                                                                          "array",
                                                                          "number",
                                                                          "string"
                                                                      ],
                                                             "items":  {

                                                                       }
                                                         }
                                             }
                                }
             }
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_recorded_crash

Inspect a supplied Linux ELF64 amd64 or AArch64 little-endian core without launching its target or attaching to any live PID. Returns all recorded segments, raw note owners/descriptors and source ranges, every Linux CORE PRSTATUS thread/register and SIGINFO fields inline with SHA-256 Evidence. Historical PID is recorded metadata; current process/file identity, note coverage and signal-thread associations stay unknown. Optionally add derived core-only GDB/pwndbg mapping candidates through explicit BYO configuration; zero map flags mean unknown permissions. Uses the same stable snapshot for both stages, bounded complete output and owned cleanup. Linux x64 host profile: unchanged pwntools 4.15.0/pyelftools 0.33/Unicorn 2.1.2; optional unchanged pwndbg 2026.09.15. No implicit executable/library pairing, GDB command language, host configuration changes or dependency installation. pwntools_timeout_ms is each owned pwntools command's deadline (omitted means 30000 ms, range 1..2147483647, next call). cpuSeconds 30 and the output, diagnostic, and address-space byte allowances have no runtime override.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to a supplied Linux ELF core"
                                },
                       "include_debugger_context":  {
                                                        "type":  "boolean",
                                                        "description":  "Add core-only mapping candidates through caller-supplied GDB/pwndbg"
                                                    },
                       "pwntools_timeout_ms":  {
                                                   "type":  "integer",
                                                   "description":  "Deadline in milliseconds for each owned pwntools command in this call. Omitted means 30000. Integer from 1 to 2147483647. Applies on the next call. Does not raise cpuSeconds 30 or the output (67108864 bytes), diagnostic (1048576 bytes), or address-space (3221225472 bytes) allowances. Artifact admission stays REA_PWNTOOLS_MAX_INPUT_BYTES (default 33554432 bytes)."
                                               }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_rust_binary

Inspect the cargo-auditable crate dependency list embedded in an explicit local ELF, PE or thin Mach-O image, or the slice of a universal Mach-O named by architecture (read without lipo; artifact stays the universal file, selected_slice records the slice and offsets stay file offsets). Returns the artifact SHA-256, the .dep-v0 section file offset and size, compressed and uncompressed sizes, format version, packages in their original order, the root package index, and dependency edges by index. Omitted or explicit complete detail preserves the full report. Optional detail summary retains that complete Evidence and returns source ranges, counts and parent_evidence_id. Optional view directly selects packages or edges in this call; it cannot be combined with explicit complete detail. Query the same retained facts with inspect_analysis_view kind rust: exact names/versions or package indices, edge from_indices/to_indices, fields and matching-row pagination. Row indices are original and scoped to the parent Evidence; empty filters select none. Default package fields omit dependency vectors; explicitly select dependencies for direct index references. Every matching observed version is retained. No current runtime state is inferred from historical reads. Reads zlib-compressed JSON without installing Rust, opening a disassembler, executing the target, writing temporary files or accessing the network. A missing section is null metadata with a limitation and is not proof that the binary has no dependencies. A rustc commit hash is reported only as an inference when a /rustc/<40-hex>/ path is present in read-only data. Resource guards bound files to 256 MiB, compressed dependency streams to 1 MiB, inflated JSON to the smaller of 8 MiB and 64 times the consumed compressed-stream length, and the complete report to 8 MiB. Malformed, unsupported and oversized inputs receive typed failures rather than partial success.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute filesystem path to the selected executable or library"
                                },
                       "architecture":  {
                                            "type":  "string",
                                            "description":  "CPU family of the slice to read from a universal (FAT) Mach-O. Required for universal files and rejected for every other image."
                                        },
                       "detail":  {
                                      "type":  "string"
                                  },
                       "view":  {
                                    "type":  "object"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## inspect_signal_decoders

Discover installed sigrok protocol decoders or inspect one decoder's channel/option help on Linux. Omit decoder to list ID/name pairs; supply its exact ID for literal help lines with source line numbers. query is a case-insensitive literal substring using Unicode simple case folding; catalog queries match the ID or name independently. Page matches with offset/limit (default 50; null returns all remaining). Reports tool identity, version, diagnostics and examined/matching coverage. Uses configured REA_SIGROK_COMMAND and prlimit; no recording, binary target, hardware scan or acquisition. Each call probes again; decoder modules are trusted, not fully fingerprinted. Use returned IDs/options with decode_signal_recording.

```json
{
    "type":  "object",
    "properties":  {
                       "decoder":  {
                                       "anyOf":  [
                                                     {
                                                         "type":  "string",
                                                         "maxLength":  256,
                                                         "pattern":  "^[a-zA-Z0-9_+\\x2d]+$"
                                                     },
                                                     {
                                                         "type":  "null"
                                                     }
                                                 ]
                                   },
                       "query":  {
                                     "type":  "string",
                                     "maxLength":  8192
                                 },
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "limit":  {
                                     "anyOf":  [
                                                   {
                                                       "type":  "integer",
                                                       "exclusiveMinimum":  0,
                                                       "maximum":  9007199254740991
                                                   },
                                                   {
                                                       "type":  "null"
                                                   }
                                               ]
                                 }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_signature

Inspect the selected Mach-O architecture's code-signing identity, hashes, authorities, designated requirement, entitlements and hardened-runtime state from a temporary digest-verified byte snapshot. Other slices are checked for unsigned state, with their metadata coverage stated separately. Exact command provenance is retained. native_command_timeout_ms is each codesign command's wall clock (omitted means 60000 ms). It does not raise the fixed 67108864-byte output allowance.

```json
{
    "type":  "object",
    "properties":  {
                       "native_command_timeout_ms":  {
                                                         "type":  "integer",
                                                         "minimum":  1,
                                                         "maximum":  2147483647
                                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_sqlite_database

Inspect an explicit local SQLite database snapshot and its existing sibling WAL without modifying source files or opening a native analysis provider. Requires Node.js 24.x >=24.15 or 26+ with native DatabaseSync.limits support, checked by the worker. Returns observed Evidence with database/WAL SHA-256 and a compact schema inventory. Selecting an exact ordinary table expands only its schema and samples rows (default 20, subject to byte and worker resource limits). Select exact columns in the requested order to avoid unrelated or large values; schema_scope=database explicitly expands the whole schema; returned rows retain 64-bit integers, text bytes and blobs with explicit truncation. Adjust worker_timeout_ms (default 30000 ms) for worker startup, queries and reply writing; allow separate client headroom for snapshot acquisition, reply reads, cleanup and delivery. The result retains the actual budget; byte/heap safety limits stay fixed. No arbitrary SQL, view or virtual-table execution, extension loading, database repair, encryption support or live-snapshot atomicity claim. At most two inspections per provider are admitted; occupied capacity is retryable. An isolated temporary copy and owned worker enforce input, value/record, SQL-statement, JSON-reply, JavaScript-heap and deadline limits; overall native heap is not capped. Supply a quiescent snapshot for coherent database/WAL evidence.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute path to an offline SQLite database snapshot"
                                },
                       "worker_timeout_ms":  {
                                                 "type":  "integer",
                                                 "description":  "Deadline in milliseconds for worker startup, queries and reply writing (default 30000), not snapshot acquisition or the whole call. Allow client headroom for acquisition, reply reads, cleanup and delivery. Fixed byte/heap limits do not change."
                                             },
                       "table":  {
                                     "type":  "string",
                                     "description":  "Exact ordinary table name; views and virtual/shadow tables are not executed"
                                 },
                       "columns":  {
                                       "type":  "array",
                                       "items":  {
                                                     "type":  [
                                                                  "null",
                                                                  "boolean",
                                                                  "object",
                                                                  "array",
                                                                  "number",
                                                                  "string"
                                                              ],
                                                     "items":  {

                                                               }
                                                 },
                                       "description":  "Exact row columns in caller-selected order; requires table. Omit to sample all visible columns."
                                   },
                       "schema_scope":  {
                                            "type":  "string",
                                            "description":  "Default: compact inventory without a table, or detailed selected-table schema with a table. selected_table requires table. Database explicitly expands all stored declarations and supported columns."
                                        },
                       "row_limit":  {
                                         "type":  "integer",
                                         "description":  "Selected-table sample size, default 20; subject to byte and worker resource limits"
                                     }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "When schema_scope is selected_table, also provide table. When row_limit is provided, also provide table. When columns is provided, also provide table. Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_wasm_artifact

Validate an explicit local WASM artifact with caller-supplied WABT 1.0.42. Optional custom-section interpretation errors do not invalidate the core module; warnings and name-section interpretation status remain in the result. Omitted or complete detail returns exact byte SHA-256, sections, upstream import/export WAT forms and decoded WAT inline as Evidence. For counts, validation and WAT identity without WAT text, pass detail: "summary" on this first call; it retains the complete original Evidence and returns the same typed summary as inspect_analysis_view, avoiding oversized complete delivery. Read exact imports/exports or WAT pages with inspect_analysis_view using normalized_result.parent_evidence_id. Delivery selection does not change analysis, provider inputs or source identity. Adjust command_timeout_ms for each WABT command (default 30000 ms), separately from the client deadline; input/output safety limits stay fixed. Optionally associate selected JavaScript glue literals with explicit local candidates while preserving ambiguous URL/basename matches. Configure absolute REA_WABT_BIN_DIRECTORY. No installation, implicit fetch, WASM execution or original-source claim.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute selected local WASM artifact path"
                                },
                       "glue_paths":  {
                                          "type":  "array",
                                          "items":  {
                                                        "type":  [
                                                                     "null",
                                                                     "boolean",
                                                                     "object",
                                                                     "array",
                                                                     "number",
                                                                     "string"
                                                                 ],
                                                        "items":  {

                                                                  }
                                                    }
                                      },
                       "candidate_paths":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  [
                                                                          "null",
                                                                          "boolean",
                                                                          "object",
                                                                          "array",
                                                                          "number",
                                                                          "string"
                                                                      ],
                                                             "items":  {

                                                                       }
                                                         }
                                           },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "description":  "Deadline in milliseconds for each WABT version/analysis command (default 30000), not the whole inspection. Allow additional client time for all commands, file checks and cleanup. Input/output safety limits remain fixed."
                                              },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "complete returns the complete analysis Evidence inline. summary retains that complete Evidence in this session and returns its inspect_analysis_view summary; inspect pages, items or facets through normalized_result.parent_evidence_id."
                                  }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_web_event_listeners

Inspect listeners registered directly on the first main-document CSS match in one selected CDP page. Returns event flags, native callback locations, source ownership and exact script text/digests inline. Does not dispatch events, evaluate JavaScript or invoke handlers. Delegated ancestor/framework listeners and execution history remain unknown. Releases REA's object group and transport, preserving the external page.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              },
                       "selector":  {
                                        "type":  "string",
                                        "minLength":  1
                                    }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id",
                     "selector"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_web_network_capture

Explicit record_ordinals also decode retained protobuf/gRPC bodies whose declared content type is recognized. Optional protobuf selects exact body_pointers, framing, and a caller-supplied local FileDescriptorSet with message_type. Native gRPC and gRPC-web framing remain distinct. Wire fields are flat byte-range facts; ambiguous readings stay candidates, compressed payloads stay unknown. Page retained decoding with inspect_analysis_view kind protobuf, collection bodies/frames/fields, exact batch selectors and fields. Large text and schema values require explicit fields selection in those pages. Request detail summary for capture identity and counts; then page retained records with inspect_analysis_view kind capture (preview or complete), avoiding repeated decoding. Default detail complete preserves existing inline delivery. Inspect retained local HAR 1.2, native mitmproxy, Fiddler SAZ or aioquic 1.5.0 JSON qlog 0.3 events without contacting recorded URLs. Returns original ordinals, pointers/byte ranges/archive member names, preserved producer fields, precise numeric representations and exact exposed binary bytes inline. Excludes known transport credentials and explicitly marked values. Decodes every record before optional ordinal selection; malformed or oversized captures return no partial success. Duplicate JSON members are invalid; prototype-named members such as __proto__ are preserved as ordinary own JSON members without prototype mutation. Qlog reports trace-scoped STREAM interval unions and endpoint loss declarations; it does not recover bodies, prove network loss or correlate PCAP. Other qlog layouts are unsupported. Uses pinned upstream parsers, owned temporary files and bounded offline processes. SAZ preserves HTTP fields, XML metadata and encoded body/message bytes without replay or HAR conversion; encrypted archives are unsupported. Native mitmproxy requires caller-supplied mitmdump 12.2.3 on Linux. Browser transaction IDs, runtime attribution and original bytes of HAR Unicode text remain unknown.

```json
{
    "type":  "object",
    "properties":  {
                       "capture_path":  {
                                            "type":  "string",
                                            "description":  "Absolute local HAR, mitmproxy, Fiddler SAZ or aioquic qlog capture path."
                                        },
                       "format":  {
                                      "type":  "string",
                                      "description":  "Declared input artifact format."
                                  },
                       "record_ordinals":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  [
                                                                          "null",
                                                                          "boolean",
                                                                          "object",
                                                                          "array",
                                                                          "number",
                                                                          "string"
                                                                      ],
                                                             "items":  {

                                                                       }
                                                         },
                                               "description":  "Zero-based producer record ordinals, in caller-selected order. Omit to inspect every record."
                                           },
                       "protobuf":  {
                                        "type":  "object"
                                    },
                       "sensitive_values":  {
                                                "type":  "array",
                                                "items":  {
                                                              "type":  [
                                                                           "null",
                                                                           "boolean",
                                                                           "object",
                                                                           "array",
                                                                           "number",
                                                                           "string"
                                                                       ],
                                                              "items":  {

                                                                        }
                                                          },
                                                "description":  "Literal text excluded from capture payloads, byte/numeric projections, producer coordinates, artifact paths/digests and diagnostic text. Declaration strings are never persisted in Evidence parameters. Canonical contract keys, enums and provider identities remain intact."
                                            },
                       "capture_timeout_ms":  {
                                                  "type":  "integer",
                                                  "description":  "Capture wall-clock deadline in milliseconds (default 30000), from decoder start through process exit. Omitted means 30000 and applies on the next capture. deadlineExpired returns no partial capture. Does not raise the fixed 33554432-byte input budget, 100663296-byte reply budget, or 64-level nesting cap. Those have no runtime override."
                                              },
                       "detail":  {
                                      "type":  "string",
                                      "description":  "complete returns the complete analysis Evidence inline. summary retains that complete Evidence in this session and returns its inspect_analysis_view summary; inspect pages, items or facets through normalized_result.parent_evidence_id."
                                  }
                   },
    "required":  [
                     "capture_path",
                     "format"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_web_page

Passively inspect one selected page target through CDP without evaluating JavaScript, navigating, clicking, closing, or mutating the page. If no origin filter is supplied, capture is scoped to the target's current origin. Returns DOM structure, accessibility, scripts, resources, attach-window network and console metadata, workers, and redacted storage inventory as Evidence.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              },
                       "observation_ms":  {
                                              "type":  "integer",
                                              "minimum":  0,
                                              "maximum":  9007199254740991
                                          },
                       "include_accessibility_text":  {
                                                          "type":  "boolean"
                                                      },
                       "include_console_text":  {
                                                    "type":  "boolean"
                                                },
                       "include_json_body_shapes":  {
                                                        "type":  "boolean"
                                                    },
                       "include_websocket_shapes":  {
                                                        "type":  "boolean"
                                                    },
                       "include_storage_keys":  {
                                                    "type":  "boolean"
                                                },
                       "include_storage_fingerprints":  {
                                                            "type":  "boolean"
                                                        },
                       "include_script_sources":  {
                                                      "type":  "boolean"
                                                  }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Provide a complete input group: cdp_endpoint + target_id + [allowed_origins] + [command_timeout_ms] + [observation_ms] + [include_accessibility_text] + [include_console_text] + [include_json_body_shapes] + [include_websocket_shapes] + [include_storage_keys] + [include_storage_fingerprints] + [include_script_sources]; cdp_endpoint + target_id + include_script_sources + [allowed_origins] + [command_timeout_ms] + [observation_ms] + [include_accessibility_text] + [include_console_text] + [include_json_body_shapes] + [include_websocket_shapes] + [include_storage_keys] + [include_storage_fingerprints].",
    "minProperties":  2,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## inspect_with_objdump

Run caller-selected GNU objdump inspection against a local artifact, or request operation=architectures without a path to list BFD targets advertised by the installed binary. For operation=disassemble, optional symbol selects native symbol-scoped disassembly before output collection; omission preserves all code sections. Matching and symbol boundaries follow GNU objdump. Preserves decoded stdout and stderr inline with SHA-256 Evidence for artifact inspections, up to a shared 16 MiB combined limit; invalid UTF-8 bytes are represented by replacement characters and flagged in Evidence limitations. output_truncated reports output beyond that limit and completion_status is unknown if buffer enforcement terminated the process. The raw output is authoritative and REA does not claim a stable normalized format. DWARF does not follow local debug links by default. Set follow_debug_links to true to follow them; identities of external debug files are not bound to Evidence. REA clears DEBUGINFOD_URLS for objdump so DWARF processing does not make debuginfod network requests. Default detail complete returns original Evidence. Explicit detail summary retains the complete parent and returns its command observation once, without the raw mirror; parent_evidence_id supports later inspect_analysis_view summary queries and complete Evidence export. Architecture output is subjectless and does not establish support for any artifact. Optional command_timeout_ms is the per-phase wall clock in milliseconds (default 120000, range 1..2147483647); omitted means 120000 and the value applies on the next call. It does not raise the 16 MiB output cap.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "operation":  {
                                         "anyOf":  [
                                                       {
                                                           "type":  "string",
                                                           "enum":  [
                                                                        "file_headers",
                                                                        "section_headers",
                                                                        "symbols",
                                                                        "relocations",
                                                                        "dwarf",
                                                                        "disassemble_all"
                                                                    ]
                                                       },
                                                       {
                                                           "type":  "string",
                                                           "const":  "disassemble"
                                                       },
                                                       {
                                                           "type":  "string",
                                                           "const":  "architectures"
                                                       }
                                                   ]
                                     },
                       "follow_debug_links":  {
                                                  "type":  "boolean"
                                              },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  },
                       "symbol":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "pattern":  "^[^\\x00]*$"
                                  }
                   },
    "required":  [
                     "operation"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Provide a complete input group: path + operation + [follow_debug_links] + [command_timeout_ms] + [detail]; path + operation + [follow_debug_links] + [command_timeout_ms] + [symbol] + [detail]; operation + [command_timeout_ms] + [detail].",
    "minProperties":  1,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## install_adb_package

Install one local APK on the device (`adb install [-r]`), recording the APK's host digest and byte size. A device-mutating tool: `replace_requested` records whether `-r` was selected; `replaced` is null because install success does not establish prior package state. The device's own Success/Failure verdict is preserved with its reason.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "apk_path":  {
                                        "type":  "string",
                                        "minLength":  1,
                                        "pattern":  "^[^\\x00]*$"
                                    },
                       "replace":  {
                                       "type":  "boolean"
                                   }
                   },
    "required":  [
                     "serial",
                     "apk_path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## instrument_with_frida

Run one caller-authored Frida script against an explicitly selected process and detach when the observation window ends. The CLI and MCP share this ephemeral workflow; use MCP session operations for persistent sessions. Remote authentication is used only for this call and excluded from Evidence.

```json
{
    "type":  "object",
    "properties":  {
                       "mode":  {
                                    "type":  "string",
                                    "enum":  [
                                                 "attach",
                                                 "spawn"
                                             ]
                                },
                       "device_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "remote":  {
                                      "type":  "object",
                                      "properties":  {
                                                         "address":  {
                                                                         "type":  "string",
                                                                         "minLength":  1,
                                                                         "maxLength":  2048
                                                                     },
                                                         "token":  {
                                                                       "type":  "string",
                                                                       "minLength":  1
                                                                   },
                                                         "certificate":  {
                                                                             "type":  "string",
                                                                             "minLength":  1
                                                                         },
                                                         "origin":  {
                                                                        "type":  "string",
                                                                        "minLength":  1
                                                                    },
                                                         "keepalive_interval":  {
                                                                                    "type":  "integer",
                                                                                    "exclusiveMinimum":  0,
                                                                                    "maximum":  9007199254740991
                                                                                }
                                                     },
                                      "required":  [
                                                       "address"
                                                   ],
                                      "additionalProperties":  false
                                  },
                       "pid":  {
                                   "type":  "integer",
                                   "exclusiveMinimum":  0,
                                   "maximum":  9007199254740991
                               },
                       "program":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "pattern":  "^[^\\x00]*$"
                                   },
                       "argv":  {
                                    "type":  "array",
                                    "items":  {
                                                  "type":  "string"
                                              }
                                },
                       "source_kind":  {
                                           "type":  "string",
                                           "enum":  [
                                                        "inline",
                                                        "file"
                                                    ]
                                       },
                       "source":  {
                                      "type":  "string"
                                  },
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "duration_ms":  {
                                           "type":  "integer",
                                           "minimum":  0,
                                           "maximum":  60000
                                       }
                   },
    "required":  [
                     "mode",
                     "source_kind"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## list_adb_devices

List attached devices exactly as `adb devices -l` reports them: serial, state, transport, model metadata, and an inferred emulator/physical kind with its evidence basis. Emulators and USB or network devices appear alike; REA neither starts nor manages them. May start the local adb server on first use.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_adb_directory

List the immediate children of one absolute device directory, including hidden entries: names, kinds, permissions, owner, group, sizes, dates, and symlink targets. Names and targets preserve embedded newlines. Recognizes the standard errors for absent paths and denied reads. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "device_path":  {
                                           "type":  "string",
                                           "minLength":  1,
                                           "pattern":  "^\\/[^\\x00]*$"
                                       }
                   },
    "required":  [
                     "serial",
                     "device_path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_adb_features

List the device's declared hardware and software features (`pm list features`) for device fingerprinting. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_adb_packages

List installed packages on one device with each base APK's device path, optionally scoped to third-party or system packages. Paths are device filesystem paths, not host-local. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "scope":  {
                                     "type":  "string",
                                     "enum":  [
                                                  "all",
                                                  "third_party",
                                                  "system"
                                              ]
                                 }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_adb_processes

List running processes exactly as `ps -A` reports them: user, pid, ppid, rss, state, and name. Useful for pairing with instrumentation lanes. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_adb_services

List the device's binder services with their interfaces (`service list`). Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_architectures

List thin or universal Mach-O slices with offsets, sizes, alignment, explicit coverage, and native-tool provenance. native_command_timeout_ms is the lipo wall clock (omitted means 60000 ms). It does not raise the fixed 67108864-byte output allowance.

```json
{
    "type":  "object",
    "properties":  {
                       "native_command_timeout_ms":  {
                                                         "type":  "integer",
                                                         "minimum":  1,
                                                         "maximum":  2147483647
                                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_bookmarks

List every bookmark in the selected Hopper document as address and name pairs; unnamed bookmarks retain a null name. Use bookmarks as analyst-authored navigation aids; this does not discover code references.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## list_browser_targets

List page targets exposed by the selected loopback Chrome DevTools Protocol endpoint. Optionally filter by exact origin. URLs preserve query values and fragments; only URL userinfo credentials are removed. HTTP discovery and browser-socket discovery use the fixed 10-second discovery deadline, then the socket closes without a later command. This operation accepts no command_timeout_ms; that listing deadline has no runtime override.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           }
                   },
    "required":  [
                     "cdp_endpoint"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_cutter_sessions

List live REA bridge sessions hosted by upstream Cutter instances, including observed document identity and bridge-directory security status. Discovery admits at most 256 descriptor candidates (16 MiB at the descriptor byte limit). That candidate budget is separate from REA_BRIDGE_MAX_REQUEST_BYTES and has no runtime override. Discovery reads and probes at most eight concurrently, and reports bridge_directory_capacity_exceeded rather than claiming no sessions when that budget is exceeded.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_documents

List provider program or document identities. Hopper may expose several documents; a Ghidra headless session contains exactly its one imported Program.

```json
{
    "type":  "object",
    "properties":  {

                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## list_electron_targets

List every Electron file:// page target from a selected loopback CDP endpoint. Local file paths are canonicalized after symlink resolution. Selecting an endpoint exposes every eligible target and its local path metadata.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        }
                   },
    "required":  [
                     "cdp_endpoint"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_frida_devices

List Frida devices currently available to this host, optionally connecting to the caller-selected remote Frida endpoint. Remote authentication options are used for this call only and are never returned or recorded.

```json
{
    "type":  "object",
    "properties":  {
                       "remote":  {
                                      "type":  "object",
                                      "properties":  {
                                                         "address":  {
                                                                         "type":  "string",
                                                                         "minLength":  1,
                                                                         "maxLength":  2048
                                                                     },
                                                         "token":  {
                                                                       "type":  "string",
                                                                       "minLength":  1
                                                                   },
                                                         "certificate":  {
                                                                             "type":  "string",
                                                                             "minLength":  1
                                                                         },
                                                         "origin":  {
                                                                        "type":  "string",
                                                                        "minLength":  1
                                                                    },
                                                         "keepalive_interval":  {
                                                                                    "type":  "integer",
                                                                                    "exclusiveMinimum":  0,
                                                                                    "maximum":  9007199254740991
                                                                                }
                                                     },
                                      "required":  [
                                                       "address"
                                                   ],
                                      "additionalProperties":  false
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_frida_processes

List processes observed on a selected Frida device or caller-supplied remote endpoint. A remote connection is closed after discovery; authentication values are never returned or recorded.

```json
{
    "type":  "object",
    "properties":  {
                       "device_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "remote":  {
                                      "type":  "object",
                                      "properties":  {
                                                         "address":  {
                                                                         "type":  "string",
                                                                         "minLength":  1,
                                                                         "maxLength":  2048
                                                                     },
                                                         "token":  {
                                                                       "type":  "string",
                                                                       "minLength":  1
                                                                   },
                                                         "certificate":  {
                                                                             "type":  "string",
                                                                             "minLength":  1
                                                                         },
                                                         "origin":  {
                                                                        "type":  "string",
                                                                        "minLength":  1
                                                                    },
                                                         "keepalive_interval":  {
                                                                                    "type":  "integer",
                                                                                    "exclusiveMinimum":  0,
                                                                                    "maximum":  9007199254740991
                                                                                }
                                                     },
                                      "required":  [
                                                       "address"
                                                   ],
                                      "additionalProperties":  false
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false,
    "description":  "Provide exactly one of device_id or remote. device_id selects a device (use \"local\" for this host); remote requires an address."
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_javascript_runtime_targets

List every eligible Node/Electron V8 Inspector target from an explicit literal-loopback endpoint. Node discovery file locations that cannot be verified are preserved as unresolved reported URLs; they do not prove filesystem identity. Listing does not attach to targets.

```json
{
    "type":  "object",
    "properties":  {
                       "inspector_endpoint":  {
                                                  "type":  "string",
                                                  "minLength":  1
                                              }
                   },
    "required":  [
                     "inspector_endpoint"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_jeb_units

List project units by stable unit path and type, with optional wildcard filter, parent restriction, and pagination. The engine caps pages at 100 units; a full page reports partial coverage because more units may exist. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "filter":  {
                                      "type":  "string"
                                  },
                       "parent_unit_path":  {
                                                "type":  "string",
                                                "minLength":  1
                                            },
                       "index":  {
                                     "type":  "integer",
                                     "minimum":  0,
                                     "maximum":  9007199254740991
                                 },
                       "count":  {
                                     "type":  "integer",
                                     "minimum":  1,
                                     "maximum":  100
                                 }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## list_names

List every analyzed memory and external symbol as address/value pairs. Provider metadata distinguishes Ghidra primary, dynamic, external, type, and source facts when available. Select detail summary for counts and retained native-inventory pages; complete remains the default.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## list_procedures

List every analyzed procedure as address/value pairs after provider analysis. Ghidra metadata distinguishes thunks and external functions; use returned addresses in later function operations. Select detail summary for counts and retained native-inventory pages; complete remains the default.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## list_segments

List segments or memory blocks using exclusive end addresses. Ghidra reports block permissions, address space, image base, initialization, and overlay facts; Hopper marks unavailable permissions explicitly.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## list_strings

List every provider-reported string object or candidate, or filter to one address, as address/value pairs with encoding, terminator status, and byte length when available. Hopper reads complete typed objects and retains its original display text as provider_value; long literals may span adjacent unterminated objects. Kuna scans mapped printable sequences of at least five characters, including unterminated fragments; references are not computed. Select detail summary for counts and retained native-inventory pages; complete remains the default.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## list_unknowns

List current residual-unknown heads in deterministic ID order, with optional exact status, severity, and domain filters. Results are complete inline by default; supply offset and limit to page the filtered order. total is the matching-head count and next_offset is null when the page exhausts the result. This is read-only; unresolved, contradicted, and non-truth dispositions remain distinct.

```json
{
    "type":  "object",
    "properties":  {
                       "status":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "open",
                                                   "investigating",
                                                   "blocked",
                                                   "contradicted",
                                                   "resolved"
                                               ]
                                  },
                       "severity":  {
                                        "type":  "string",
                                        "enum":  [
                                                     "low",
                                                     "medium",
                                                     "high",
                                                     "critical"
                                                 ]
                                    },
                       "domain":  {
                                      "type":  "string",
                                      "minLength":  1
                                  },
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "limit":  {
                                     "type":  "integer",
                                     "exclusiveMinimum":  0,
                                     "maximum":  9007199254740991
                                 }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## load_frida_script

Load caller-supplied JavaScript into a Frida session. Scripts can read or change target memory and behavior and can access the target process's capabilities. Messages and script errors are returned inline as Evidence; source content is not persisted.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      },
                       "source_kind":  {
                                           "type":  "string",
                                           "enum":  [
                                                        "inline",
                                                        "file"
                                                    ]
                                       },
                       "source":  {
                                      "type":  "string"
                                  },
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                }
                   },
    "required":  [
                     "session_id",
                     "source_kind"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## match_native_functions

Match two retained list_procedures inventories by unique exact reported symbols, then unique identical observed function bytes. Optional same-context list_names/procedure_info/read_bytes/analyze_function Evidence supplies symbol origin, exact bodies, bytes and dossier links as supplied static facts. Optional compatible CFG/call/string fingerprints from supplied dossiers propose unresolved groups only. Duplicate candidates remain unresolved groups; only_left/right mean unpaired within supplied observations, never proof of addition/removal. Default summary retains complete Evidence for filtered, selected-column, paged function-matches views. Complete detail returns every group. Existing dossier pairs provide direct retained-reference compare_functions inputs.

```json
{
    "type":  "object",
    "properties":  {
                       "left":  {
                                    "description":  "Left list_procedures Evidence or exact retained reference"
                                },
                       "right":  {
                                     "description":  "Right list_procedures Evidence or exact retained reference"
                                 },
                       "left_observations":  {
                                                 "type":  "array",
                                                 "items":  {
                                                               "type":  [
                                                                            "null",
                                                                            "boolean",
                                                                            "object",
                                                                            "array",
                                                                            "number",
                                                                            "string"
                                                                        ],
                                                               "items":  {

                                                                         }
                                                           },
                                                 "description":  "Optional same-context list_names, procedure_info, read_bytes or analyze_function Evidence"
                                             },
                       "right_observations":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  [
                                                                             "null",
                                                                             "boolean",
                                                                             "object",
                                                                             "array",
                                                                             "number",
                                                                             "string"
                                                                         ],
                                                                "items":  {

                                                                          }
                                                            },
                                                  "description":  "Optional same-context list_names, procedure_info, read_bytes or analyze_function Evidence"
                                              },
                       "detail":  {
                                      "type":  "string"
                                  }
                   },
    "required":  [
                     "left",
                     "right"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## next_address

Return the next analyzed object address after an explicit address or current cursor. This is a navigation primitive, not instruction-flow or CFG analysis.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## observe_javascript_runtime

Attach passively to one Node/Electron V8 Inspector target by supplying its loopback Inspector endpoint and target ID. The provider rechecks target presence and attachability; a Node discovery file location may remain unresolved. Loaded script locations are validated independently from Debugger.scriptParsed. Authorized file script items retain their exact Inspector URL in optional reported_url separately from verified location.file_path, and query/fragment variants remain distinct script identities. Valid Debugger.scriptFailedToParse events are counted at scripts.parse_failed_total separately from malformed protocol values. Captures script and Runtime execution-context metadata. REA never evaluates, pauses, resumes, reads source, or instruments the target; require/import edges, EventEmitter activity, and Electron IPC remain explicit unknowns. Reconcile the result with static Application Graph Evidence using reconcile_javascript_runtime. The selected endpoint exposes every target it serves to this tool.

```json
{
    "type":  "object",
    "properties":  {
                       "inspector_endpoint":  {
                                                  "type":  "string",
                                                  "minLength":  1
                                              },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "runtime_kind":  {
                                            "type":  "string",
                                            "enum":  [
                                                         "node",
                                                         "electron-main",
                                                         "electron-preload",
                                                         "electron-renderer"
                                                     ]
                                        },
                       "observation_ms":  {
                                              "type":  "integer",
                                              "minimum":  0,
                                              "maximum":  9007199254740991
                                          }
                   },
    "required":  [
                     "inspector_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## observe_native_calls

Launch the active Mach-O as an owned process under LLDB and record each entry into caller-selected functions or Objective-C methods: thread, module, symbol, load and file address, raw argument registers, selector, receiver class and optional caller frames. Every hit auto-continues. No expression is evaluated and no other process is attached. The process is killed when max_events or duration_ms is reached; exit status, captured stdout/stderr, unresolved breakpoints and limitations are returned inline. The process runs with the current user's permissions and may change files, show UI or use the network. A hardened-runtime target needs the get-task-allow entitlement.

```json
{
    "type":  "object",
    "properties":  {
                       "breakpoints":  {
                                           "type":  "array",
                                           "items":  {
                                                         "type":  [
                                                                      "null",
                                                                      "boolean",
                                                                      "object",
                                                                      "array",
                                                                      "number",
                                                                      "string"
                                                                  ],
                                                         "items":  {

                                                                   }
                                                     }
                                       },
                       "arguments":  {
                                         "type":  "array",
                                         "items":  {
                                                       "type":  [
                                                                    "null",
                                                                    "boolean",
                                                                    "object",
                                                                    "array",
                                                                    "number",
                                                                    "string"
                                                                ],
                                                       "items":  {

                                                                 }
                                                   }
                                     },
                       "environment":  {
                                           "type":  "object",
                                           "description":  "Overrides on top of the environment REA runs with."
                                       },
                       "working_directory":  {
                                                 "type":  "string"
                                             },
                       "duration_ms":  {
                                           "type":  "integer",
                                           "description":  "Observation window; the process is killed if it is still running."
                                       },
                       "max_events":  {
                                          "type":  "integer",
                                          "description":  "Stop observing and kill the process after this many recorded entries."
                                      },
                       "argument_registers":  {
                                                  "type":  "integer",
                                                  "description":  "Integer argument registers to record per entry (arm64 has 8, x86_64 has 6)."
                                              },
                       "backtrace_frames":  {
                                                "type":  "integer",
                                                "description":  "Caller frames to record per entry."
                                            }
                   },
    "required":  [
                     "breakpoints"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## observe_native_ui

Observe an already-running native app by PID and window ID, bound to the active Mach-O target. Captures a bounded accessibility tree and selected-window screenshot; OS permission denial fails without broad capture or target launch. native_ui_deadline_ms is the observation deadline (omitted means 180000 ms, range 1..2147483647, next call). It does not raise the fixed 67108864-byte output allowance or the 180000 ms wait-step cap.

```json
{
    "type":  "object",
    "properties":  {
                       "pid":  {
                                   "type":  "integer",
                                   "exclusiveMinimum":  0,
                                   "maximum":  9007199254740991
                               },
                       "window_id":  {
                                         "type":  "integer",
                                         "exclusiveMinimum":  0,
                                         "maximum":  9007199254740991
                                     },
                       "screenshot":  {
                                          "type":  "boolean"
                                      },
                       "accessibility":  {
                                             "type":  "boolean"
                                         },
                       "max_nodes":  {
                                         "type":  "integer",
                                         "exclusiveMinimum":  0,
                                         "maximum":  9007199254740991
                                     },
                       "native_ui_deadline_ms":  {
                                                     "type":  "integer",
                                                     "minimum":  1,
                                                     "maximum":  2147483647
                                                 }
                   },
    "required":  [
                     "pid",
                     "window_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## observe_web_execution

Instrument one selected CDP page for a finite, externally driven precise coverage window. Resets execution counters and prevents optimized execution until stopped. Returns complete nested UTF-16 ranges/counts, independently hashed script text and request initiator associations by session script ID. Does not navigate, click, evaluate selected code or close the externally owned page. Navigation ends document-scoped collection; workers and UI causality remain outside this claim. Emits an actual armed progress notification before external actions.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              },
                       "observation_ms":  {
                                              "type":  "integer",
                                              "minimum":  1,
                                              "maximum":  2147483647
                                          }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## observe_web_session

Arm a CDP observation window of the requested duration while the user operates the page. By default it follows the selected target's current origin; an explicit origin list controls which origins may be retained. Records navigation, redirect, lifecycle, and failure events.

```json
{
    "type":  "object",
    "properties":  {
                       "cdp_endpoint":  {
                                            "type":  "string",
                                            "minLength":  1
                                        },
                       "allowed_origins":  {
                                               "type":  "array",
                                               "items":  {
                                                             "type":  "string",
                                                             "minLength":  1
                                                         }
                                           },
                       "target_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "observation_ms":  {
                                              "type":  "integer",
                                              "minimum":  1,
                                              "maximum":  9007199254740991
                                          },
                       "command_timeout_ms":  {
                                                  "type":  "integer",
                                                  "minimum":  1,
                                                  "maximum":  2147483647
                                              }
                   },
    "required":  [
                     "cdp_endpoint",
                     "target_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## open_binary

Open a local executable, application bundle, archive, JavaScript, source map, plist, or analysis database after validation. format=dos-com explicitly interprets 1..65280 headerless bytes as a DOS COM analysis image. format=raw-binary interprets headerless bytes with a required architecture and entry_point; file offset 0 maps at that address. Omission preserves header-based detection. provider_id selects one deep provider or deterministic auto selection; the binding remains stable until close or an explicit switch, with no failure fallback. Reopening the same target and profile retains its active database and metadata edits. An optional analysis snapshot is imported atomically and must match the binary identity, concrete provider, and canonical analysis profile exactly; importing into a session with metadata mutations requires closing and reopening it first. Call close_binary when the investigation is finished.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local filesystem path for the analysis target; relative paths are rejected."
                                },
                       "format":  {
                                      "type":  "string",
                                      "description":  "Explicit interpretation: dos-com or raw-binary (headerless), or gamecube-dol (GameCube/Wii DOL, never auto-detected). Omission preserves header-based detection."
                                  },
                       "architecture":  {
                                            "type":  "string",
                                            "description":  "Caller-supplied CPU family. Required with format=raw-binary; rejected otherwise. format=gamecube-dol takes powerpc from the header, so it must be omitted."
                                        },
                       "entry_point":  {
                                           "type":  "string",
                                           "description":  "Caller-supplied load and entry address (0xhex). File offset 0 is mapped here. Required with format=raw-binary; rejected otherwise. Optional with format=gamecube-dol; must equal the DOL header entry."
                                       },
                       "provider_id":  {
                                           "type":  "string",
                                           "description":  "Exact deep-analysis provider ID, or automatic selection when omitted."
                                       },
                       "snapshot_path":  {
                                             "type":  "string",
                                             "description":  "Absolute local filesystem path for the analysis snapshot; relative paths are rejected."
                                         }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## open_bundle_resources

Open a local app bundle for read-only plist, Interface Builder, asset catalog, and keyed archive inspection without resolving or inventing its executable identity. This switches the shared session to a resource-only lane; executable analysis is unavailable until another target is opened.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "description":  "Absolute local filesystem path for the analysis target; relative paths are rejected."
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## open_jeb_project

Open or create a JEB project from an artifact file or existing .jdb2 database on the engine host. Returns the project's input-file digests and top-level units. The engine analyzes the target itself; REA does not read the file or execute it. Fails when a project is already open.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                }
                   },
    "required":  [
                     "path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## prev_address

Return the previous analyzed object start before an explicit address or current cursor. This is a navigation primitive and may fail at document boundaries; data objects and instructions remain distinct from control flow.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_address

Resolve an unambiguous procedure symbol name or provider-normalized address to its canonical entry address. External address spaces remain explicit.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_assembly

Return assembly for one analyzed procedure identified by symbol or hexadecimal address. Use when pseudocode loses calling-convention or instruction-level detail; output is currently returned as one unpaginated string. Ghidra terminal-call assumptions that may cut the analyzed body are reported in Evidence limitations.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_callees

Return the provider's resolved direct callees for one procedure identified by symbol or address. Unresolved indirect calls may be absent; use analyze_function and typed references to preserve available edge uncertainty. Ghidra terminal-call assumptions that may cut the analyzed body are reported in Evidence limitations.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_callers

Return the provider's resolved direct callers for one procedure identified by symbol or address. Results reflect completed static analysis and may omit unresolved indirect references. Ghidra terminal-call assumptions that may cut the analyzed body are reported in Evidence limitations.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_info

Return provider metadata for one procedure identified by symbol or address: entrypoint, signature, locals, size, block count, and complete inclusive body ranges when observed. Extents reflect the provider's analyzed body; Ghidra terminal-call assumptions that may cut it are reported in Evidence limitations. Hopper locals preserve observed names and signed stack displacements; source types remain unknown. Body byte count and enclosing span remain distinct; unavailable extent is explicit.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_pseudo_code

Decompile one analyzed procedure by symbol name or provider-normalized address. Ghidra also accepts file-offset:0x... selectors and falls back to a unique source-file mapping when a 0x-prefixed number resolves to no procedure; existing analysis addresses take precedence and ambiguous mappings fail explicitly. Returns provider-specific pseudocode, never original source or cross-provider text equivalence, and may return null; request procedure_assembly when instruction precision matters. Ghidra no-return assumptions may cut the analyzed body; Evidence limitations report observed terminal calls and available callee identities.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## procedure_references

Return every raw incoming or outgoing reference edge for one procedure. Endpoint procedures are resolved only from provider containment. Ghidra preserves detailed reference flags. Hopper preserves native CallReference classifications and exact endpoints while leaving unavailable detailed flags explicit.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "direction":  {
                                         "type":  "string",
                                         "enum":  [
                                                      "incoming",
                                                      "outgoing"
                                                  ]
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## project_android_application_graph

Project authenticated APK inventory_artifact Evidence into an execution-free Android application inventory. Reports exact component paths and hashes, runtime-family hints, and path-based bridge hypotheses without decoding DEX, executing the APK, or claiming observed runtime calls.

```json
{
    "type":  "object",
    "properties":  {
                       "inventory_evidence":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  [
                                                                             "null",
                                                                             "boolean",
                                                                             "object",
                                                                             "array",
                                                                             "number",
                                                                             "string"
                                                                         ],
                                                                "items":  {

                                                                          }
                                                            }
                                              }
                   },
    "required":  [
                     "inventory_evidence"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## project_apple_application_graph

Project authenticated IPA, macOS .app directory, ZIP, or DMG inventory Evidence into an execution-free Apple application inventory. Reports application roots, nested bundles with path-convention roles (app extensions, XPC services, login items, privileged helpers, system extensions, frameworks), launchd plists, exact component paths and hashes, runtime-family hints, and path-based bridge hypotheses without parsing plist/CMS semantics or claiming observed runtime calls.

```json
{
    "type":  "object",
    "properties":  {
                       "inventory_evidence":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  [
                                                                             "null",
                                                                             "boolean",
                                                                             "object",
                                                                             "array",
                                                                             "number",
                                                                             "string"
                                                                         ],
                                                                "items":  {

                                                                          }
                                                            }
                                              }
                   },
    "required":  [
                     "inventory_evidence"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## project_harmony_application_graph

Project authenticated HarmonyOS HAP, HSP, or App Pack inventory Evidence into an execution-free application inventory. Reports packaging-model hints (Stage module.json versus FA config.json), exact component paths and hashes (manifests, Ark bytecode, resources, native libraries, JavaScript, signing), App Pack child packages, runtime-family hints, and path-based N-API bridge hypotheses without decoding Ark bytecode, parsing manifest semantics, or claiming observed runtime calls.

```json
{
    "type":  "object",
    "properties":  {
                       "inventory_evidence":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  [
                                                                             "null",
                                                                             "boolean",
                                                                             "object",
                                                                             "array",
                                                                             "number",
                                                                             "string"
                                                                         ],
                                                                "items":  {

                                                                          }
                                                            }
                                              }
                   },
    "required":  [
                     "inventory_evidence"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## project_managed_application_graph

Project authenticated managed artifact, member, and native-boundary Evidence into the provider-neutral application graph without executing managed code, loading assemblies, or translating managed metadata tokens to native addresses. The graph preserves managed static observations as managed-specific nodes linked to source Evidence, with in-module calls and field-access edges; other call and field-access sites are counted in limitations.

```json
{
    "type":  "object",
    "properties":  {
                       "managed_artifact":  {
                                                "type":  "object"
                                            },
                       "managed_members":  {
                                               "type":  "object"
                                           },
                       "managed_native_boundaries":  {
                                                         "type":  "object"
                                                     }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Provide a complete input group: managed_artifact + [managed_members] + [managed_native_boundaries]; managed_members + [managed_artifact] + [managed_native_boundaries]; managed_native_boundaries + [managed_artifact] + [managed_members]. Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "minProperties":  1,
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## pull_adb_file

Pull one absolute device path (a database, shared-preferences XML, or any readable file) into a caller-selected output directory, digesting it with SHA-256. The local copy must not already exist. Read-only with respect to the device.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "device_path":  {
                                           "type":  "string",
                                           "minLength":  1,
                                           "pattern":  "^\\/[^\\x00]*$"
                                       },
                       "output_directory":  {
                                                "type":  "string",
                                                "minLength":  1,
                                                "pattern":  "^[^\\x00]*$"
                                            }
                   },
    "required":  [
                     "serial",
                     "device_path",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## pull_adb_package

Pull one installed package's complete APK set (base plus every split, as `pm path` reports) from the device into a caller-selected output directory, creating a package-named subdirectory that must not already exist. Every pulled file is digested with SHA-256; partial pulls keep successful artifacts and per-file failure reasons. The device keeps the originals.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "package":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "maxLength":  256,
                                       "pattern":  "^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)*$"
                                   },
                       "output_directory":  {
                                                "type":  "string",
                                                "minLength":  1,
                                                "pattern":  "^[^\\x00]*$"
                                            }
                   },
    "required":  [
                     "serial",
                     "package",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## push_adb_file

Push one local file to an absolute device path. If the destination is an existing directory, adb places the file inside it under the local basename and the result reports that file path. A device-mutating tool: checks for an existing destination file before transfer and refuses it unless overwrite is explicitly requested; this check is not atomic and another process can create the path before adb writes. The private staged copy remains non-writable and preserves source executable bits. When the device provides sha256sum, the transfer is verified by comparing it with the local digest.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "local_path":  {
                                          "type":  "string",
                                          "minLength":  1,
                                          "pattern":  "^[^\\x00]*$"
                                      },
                       "device_path":  {
                                           "type":  "string",
                                           "minLength":  1,
                                           "pattern":  "^\\/[^\\x00]*$"
                                       },
                       "overwrite":  {
                                         "type":  "boolean"
                                     }
                   },
    "required":  [
                     "serial",
                     "local_path",
                     "device_path"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## read_adb_logcat

Read a bounded dump of the device log (`logcat -d -t`) for one buffer, optionally restricted to a process id. No streaming, no clearing; the newest lines are kept when the count cap trims. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "count":  {
                                     "type":  "integer",
                                     "minimum":  1,
                                     "maximum":  10000
                                 },
                       "buffer":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "main",
                                                   "system",
                                                   "radio",
                                                   "events",
                                                   "crash"
                                               ]
                                  },
                       "pid":  {
                                   "type":  "integer",
                                   "exclusiveMinimum":  0,
                                   "maximum":  9007199254740991
                               }
                   },
    "required":  [
                     "serial"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## read_adb_setting

Read one settings value (`settings get`) from the system, secure, or global namespace. Absent keys report the device's own null observation. Read-only; writing settings is not exposed.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "namespace":  {
                                         "type":  "string",
                                         "enum":  [
                                                      "system",
                                                      "secure",
                                                      "global"
                                                  ]
                                     },
                       "key":  {
                                   "type":  "string",
                                   "minLength":  1,
                                   "maxLength":  256,
                                   "pattern":  "^[A-Za-z0-9._\\x2d]+$"
                               }
                   },
    "required":  [
                     "serial",
                     "namespace",
                     "key"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## read_bytes

Read analyzed bytes from one provider-normalized virtual address. The hexadecimal payload reports the exact returned length; incomplete reads remain explicit and unsupported provider APIs return typed capability unavailability.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "length":  {
                                      "type":  "integer",
                                      "minimum":  1,
                                      "maximum":  9007199254740991
                                  },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## read_function_instructions

Return every raw instruction for one analyzed procedure. Instruction text and body extent are provider-specific. Ghidra terminal-call assumptions that may cut the analyzed body are reported in limitations.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string"
                                     },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## reconcile_javascript_runtime

Reconcile verified static JavaScript application graphs with existing passive web/Electron CDP, passive V8 Inspector, or provider-owned active Electron Evidence. Active Electron captures contribute an explicitly partial target-only runtime record; they never invent renderer scripts, frames, workers, or execution claims. Exact captured-source digests take priority over caller-declared file/URL mappings; target, frame, script, and worker ambiguity remains explicit, and source-map authority stays separate.

```json
{
    "type":  "object",
    "properties":  {
                       "static_layers":  {
                                             "type":  "array",
                                             "items":  {
                                                           "type":  [
                                                                        "null",
                                                                        "boolean",
                                                                        "object",
                                                                        "array",
                                                                        "number",
                                                                        "string"
                                                                    ],
                                                           "items":  {

                                                                     }
                                                       }
                                         },
                       "runtime_observations":  {
                                                    "type":  "array",
                                                    "items":  {
                                                                  "type":  [
                                                                               "null",
                                                                               "boolean",
                                                                               "object",
                                                                               "array",
                                                                               "number",
                                                                               "string"
                                                                           ],
                                                                  "items":  {

                                                                            }
                                                              }
                                                }
                   },
    "required":  [
                     "static_layers",
                     "runtime_observations"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## record_unknown

Create one deterministic residual unknown and immutable mutation evidence. Validates all evidence and relationship references, and rejects duplicate stable identity.

```json
{
    "type":  "object",
    "properties":  {
                       "question":  {
                                        "type":  "string",
                                        "minLength":  1
                                    },
                       "severity":  {
                                        "type":  "string",
                                        "enum":  [
                                                     "low",
                                                     "medium",
                                                     "high",
                                                     "critical"
                                                 ]
                                    },
                       "domain":  {
                                      "type":  "string",
                                      "minLength":  1
                                  },
                       "comparison_conclusion_scope":  {
                                                           "type":  "object",
                                                           "properties":  {
                                                                              "comparison_evidence_id":  {
                                                                                                             "type":  "string",
                                                                                                             "pattern":  "^ev_[a-f0-9]{64}$"
                                                                                                         },
                                                                              "dimensions":  {
                                                                                                 "minItems":  1,
                                                                                                 "type":  "array",
                                                                                                 "items":  {
                                                                                                               "anyOf":  [
                                                                                                                             {
                                                                                                                                 "type":  "string",
                                                                                                                                 "const":  "overall"
                                                                                                                             },
                                                                                                                             {
                                                                                                                                 "type":  "string",
                                                                                                                                 "enum":  [
                                                                                                                                              "identity",
                                                                                                                                              "pseudocode",
                                                                                                                                              "assembly",
                                                                                                                                              "comments",
                                                                                                                                              "calls",
                                                                                                                                              "references",
                                                                                                                                              "strings_names",
                                                                                                                                              "cfg"
                                                                                                                                          ]
                                                                                                                             }
                                                                                                                         ],
                                                                                                               "type":  "string"
                                                                                                           }
                                                                                             }
                                                                          },
                                                           "required":  [
                                                                            "comparison_evidence_id",
                                                                            "dimensions"
                                                                        ],
                                                           "additionalProperties":  false
                                                       },
                       "required_authority":  {
                                                  "anyOf":  [
                                                                {
                                                                    "type":  "string",
                                                                    "enum":  [
                                                                                 "shipped-artifact",
                                                                                 "controlled-replay",
                                                                                 "historical-reference",
                                                                                 "external-service",
                                                                                 "analyst-inference"
                                                                             ]
                                                                },
                                                                {
                                                                    "type":  "null"
                                                                }
                                                            ]
                                              },
                       "required_confidence":  {
                                                   "type":  "string",
                                                   "enum":  [
                                                                "observed",
                                                                "derived",
                                                                "inferred"
                                                            ]
                                               },
                       "required_environment":  {
                                                    "anyOf":  [
                                                                  {
                                                                      "type":  "object",
                                                                      "properties":  {
                                                                                         "id":  {
                                                                                                    "anyOf":  [
                                                                                                                  {
                                                                                                                      "type":  "string",
                                                                                                                      "minLength":  1
                                                                                                                  },
                                                                                                                  {
                                                                                                                      "type":  "null"
                                                                                                                  }
                                                                                                              ]
                                                                                                },
                                                                                         "platform":  {
                                                                                                          "anyOf":  [
                                                                                                                        {
                                                                                                                            "type":  "string",
                                                                                                                            "minLength":  1
                                                                                                                        },
                                                                                                                        {
                                                                                                                            "type":  "null"
                                                                                                                        }
                                                                                                                    ]
                                                                                                      },
                                                                                         "architecture":  {
                                                                                                              "anyOf":  [
                                                                                                                            {
                                                                                                                                "type":  "string",
                                                                                                                                "minLength":  1
                                                                                                                            },
                                                                                                                            {
                                                                                                                                "type":  "null"
                                                                                                                            }
                                                                                                                        ]
                                                                                                          },
                                                                                         "isolation":  {
                                                                                                           "anyOf":  [
                                                                                                                         {
                                                                                                                             "type":  "string",
                                                                                                                             "enum":  [
                                                                                                                                          "none",
                                                                                                                                          "process",
                                                                                                                                          "container",
                                                                                                                                          "virtual-machine"
                                                                                                                                      ]
                                                                                                                         },
                                                                                                                         {
                                                                                                                             "type":  "null"
                                                                                                                         }
                                                                                                                     ]
                                                                                                       }
                                                                                     },
                                                                      "required":  [
                                                                                       "id",
                                                                                       "platform",
                                                                                       "architecture",
                                                                                       "isolation"
                                                                                   ],
                                                                      "additionalProperties":  false
                                                                  },
                                                                  {
                                                                      "type":  "null"
                                                                  }
                                                              ]
                                                },
                       "recommended_probes":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  "object",
                                                                "properties":  {
                                                                                   "operation":  {
                                                                                                     "type":  "string",
                                                                                                     "minLength":  1
                                                                                                 },
                                                                                   "rationale":  {
                                                                                                     "type":  "string",
                                                                                                     "minLength":  1
                                                                                                 }
                                                                               },
                                                                "required":  [
                                                                                 "operation",
                                                                                 "rationale"
                                                                             ],
                                                                "additionalProperties":  false
                                                            }
                                              },
                       "relationships":  {
                                             "type":  "array",
                                             "items":  {
                                                           "type":  "object",
                                                           "properties":  {
                                                                              "type":  {
                                                                                           "type":  "string",
                                                                                           "enum":  [
                                                                                                        "depends-on",
                                                                                                        "related-to",
                                                                                                        "contradicts"
                                                                                                    ]
                                                                                       },
                                                                              "unknown_id":  {
                                                                                                 "type":  "string",
                                                                                                 "pattern":  "^unk_[a-f0-9]{64}$"
                                                                                             }
                                                                          },
                                                           "required":  [
                                                                            "type",
                                                                            "unknown_id"
                                                                        ],
                                                           "additionalProperties":  false
                                                       }
                                         },
                       "supporting_evidence_ids":  {
                                                       "type":  "array",
                                                       "items":  {
                                                                     "type":  "string",
                                                                     "pattern":  "^ev_[a-f0-9]{64}$"
                                                                 }
                                                   },
                       "contradicting_evidence_ids":  {
                                                          "type":  "array",
                                                          "items":  {
                                                                        "type":  "string",
                                                                        "pattern":  "^ev_[a-f0-9]{64}$"
                                                                    }
                                                      }
                   },
    "required":  [
                     "question",
                     "severity",
                     "domain",
                     "required_authority",
                     "required_confidence",
                     "required_environment",
                     "recommended_probes",
                     "relationships"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## recover_javascript_sources

Recover readable modules from local UTF-8 JavaScript scripts/bundles into an absent directory, using caller-supplied Wakaru ^1.13.0 (verified with 1.14.0) on Linux x64. Use additional_paths for related chunks; each input retains its own snapshot and provenance. Returns original/derived digests, extraction byte ranges, emitted source maps, warnings and analysis_input inline. Structural splitting may fall back to one module; original names and runtime equivalence remain unknown. No application code execution. Requires util-linux prlimit; one worker, 1 GiB address-space ceiling, 120-second deadline, 64 MiB total input, 128 MiB output, 10000 entries and 8 MiB per diagnostic/report stream.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "additional_paths":  {
                                                "maxItems":  16,
                                                "type":  "array",
                                                "items":  {
                                                              "type":  "string",
                                                              "minLength":  1,
                                                              "pattern":  "^[^\\x00]*$"
                                                          }
                                            },
                       "output_directory":  {
                                                "type":  "string",
                                                "minLength":  1,
                                                "pattern":  "^[^\\x00]*$"
                                            },
                       "extraction_mode":  {
                                               "type":  "string",
                                               "enum":  [
                                                            "structural",
                                                            "heuristic",
                                                            "inspection"
                                                        ]
                                           },
                       "rewrite_level":  {
                                             "type":  "string",
                                             "enum":  [
                                                          "minimal",
                                                          "standard",
                                                          "aggressive"
                                                      ]
                                         }
                   },
    "required":  [
                     "path",
                     "output_directory"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## resolve_adb_packages

Find installed packages by case-insensitive substring of the package name (e.g. `whatsapp` → `com.whatsapp`), returning each match's base APK device path. The first step of an easy get: resolve, then pull_adb_package the exact name. Read-only.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "query":  {
                                     "type":  "string",
                                     "minLength":  2,
                                     "maxLength":  256
                                 }
                   },
    "required":  [
                     "serial",
                     "query"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## resolve_containing_procedure

Resolve an arbitrary address, including an interior instruction or exact external entry, to its provider-analyzed containing procedure. A negative result distinguishes outside segments from not in a procedure and is never guessed from nearby symbols.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## resolve_native_call_targets

Resolve one explicit static call site using typed provider call references. Preserves ambiguous targets as candidates and unresolved computed calls as unknown; does not execute code or traverse a call graph.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## resume_frida_session

Resume the process created by a paused Frida spawn session. Attached processes are already running and cannot be resumed through this operation.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## rizin_debug_command

Execute one unrestricted command in a persistent Rizin debugger session. Returns raw output inline with Evidence, including backend output interleaved before the completion marker. If the command exceeds the output-byte, frame-byte, or frame-count limit, `output_truncated` is true and `completion_status` is `unknown`; retained partial output is returned and the owned debugger session is stopped. Evidence names the cap that stopped capture. Unsolicited frames already queued before the command make the session unavailable because correlation is ambiguous. Commands can access local files, shell, network, plugins, and target state; effects depend on the active backend and target. In a shared MCP server, this admitted command closes any active binary-analysis session; after its effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      },
                       "command":  {
                                       "type":  "string",
                                       "minLength":  1
                                   }
                   },
    "required":  [
                     "session_id",
                     "command"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## rizin_debug_session_status

Return observed lifecycle state and bounded recent NUL-framed output and process diagnostics for one Rizin debugger session; truncation fields report discarded data, and output_invalid_utf8 reports whether invalid UTF-8 has been observed in session frame bytes; recent output may contain replacement characters.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      }
                   },
    "required":  [
                     "session_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## search_android_classes

Search complete top-level decompiler class-name rows in an explicit local APK by case-sensitive substring. Empty query inventories all rows. Repeated names remain repeated and appear in duplicate_names; the provider accepts only class names for lookup, so these rows do not identify which same-name class node a later lookup selects. Retains artifact and engine identity; no APK execution.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "query":  {
                                     "type":  "string"
                                 }
                   },
    "required":  [
                     "path",
                     "query"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## search_bytes

Find masked byte signatures in initialized analyzed memory. Hex byte pairs and ?? wildcards are supported; an optional equal-length hex mask selects bits. Bounds are inclusive. Matches are ordered by provider address and never span blocks. Coverage describes this page; reuse the same query with next_cursor to continue. Uninitialized blocks are listed separately. Ghidra only; Hopper is unavailable.

```json
{
    "type":  "object",
    "properties":  {
                       "pattern":  {
                                       "type":  "string"
                                   },
                       "mask":  {
                                    "type":  "string"
                                },
                       "document":  {
                                        "type":  "string"
                                    },
                       "start":  {
                                     "type":  "string"
                                 },
                       "end":  {
                                   "type":  "string"
                               },
                       "limit":  {
                                     "type":  "integer",
                                     "minimum":  1,
                                     "maximum":  1000
                                 },
                       "cursor":  {
                                      "type":  "string",
                                      "minLength":  1
                                  }
                   },
    "required":  [
                     "pattern"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## search_procedures

Search every analyzed procedure name using literal matching by default or regex when requested. Results are deterministic and complete. Hopper regex mode uses ECMAScript Unicode syntax in a cancellable worker. The matching deadline is REA_REGEX_MATCHING_DEADLINE_MS (a positive decimal integer of milliseconds from 1 to 2147483647, default 5000). Unset keeps the default. A deadline failure names the effective milliseconds, source (default or setting), the default, and the maximum, and leaves the API usable. The worker old-generation request is REA_REGEX_WORKER_HEAP_REQUEST_MIB (a decimal integer of mebibytes from 7 to 16384, default 128). Unset keeps the default. A positive Node --max-old-space-size, including NODE_OPTIONS, overrides that request and is not limited by the setting's floor or maximum. A heap failure names the effective request, its source (default or setting), the default, the floor, and the maximum. Select detail summary for counts and retained native-inventory pages; complete remains the default.

```json
{
    "type":  "object",
    "properties":  {
                       "pattern":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "mode":  {
                                    "type":  "string",
                                    "enum":  [
                                                 "literal",
                                                 "regex"
                                             ]
                                },
                       "case_sensitive":  {
                                              "type":  "boolean"
                                          },
                       "document":  {
                                        "type":  "string"
                                    },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "pattern"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## search_strings

Search every provider-reported string object or candidate using literal matching by default or regex when requested. Results are deterministic and complete over that provider inventory, not every text sequence in artifact bytes. Kuna scans mapped sequences of at least five characters, including unterminated fragments, without computing references. Ghidra may omit packed or undefined literals, including Rust strings; an empty result does not prove text is absent. If an address is known, read_bytes and xrefs inspect its bytes and references. Hopper retains typed byte decoding, native display, and explicit decoding unknowns. Adjacent long-literal fragments match independently. Hopper regex mode uses ECMAScript Unicode syntax in a cancellable worker. The matching deadline is REA_REGEX_MATCHING_DEADLINE_MS (a positive decimal integer of milliseconds from 1 to 2147483647, default 5000). Unset keeps the default. A deadline failure names the effective milliseconds, source (default or setting), the default, and the maximum, and leaves the API usable. The worker old-generation request is REA_REGEX_WORKER_HEAP_REQUEST_MIB (a decimal integer of mebibytes from 7 to 16384, default 128). Unset keeps the default. A positive Node --max-old-space-size, including NODE_OPTIONS, overrides that request and is not limited by the setting's floor or maximum. A heap failure names the effective request, its source (default or setting), the default, the floor, and the maximum. Select detail summary for counts and retained native-inventory pages; complete remains the default.

```json
{
    "type":  "object",
    "properties":  {
                       "pattern":  {
                                       "type":  "string",
                                       "minLength":  1
                                   },
                       "mode":  {
                                    "type":  "string",
                                    "enum":  [
                                                 "literal",
                                                 "regex"
                                             ]
                                },
                       "case_sensitive":  {
                                              "type":  "boolean"
                                          },
                       "document":  {
                                        "type":  "string"
                                    },
                       "detail":  {
                                      "type":  "string",
                                      "enum":  [
                                                   "complete",
                                                   "summary"
                                               ]
                                  }
                   },
    "required":  [
                     "pattern"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## set_address_name

Assign an analyst name to one mapped hexadecimal address and report whether the native edit succeeded and exact readback matched. Hopper rejects symbol names exceeding 1024 UTF-16 code units before mutation, and rejects names owned by another address; use set_addresses_names to explicitly move or swap labels among selected addresses. This mutates analysis metadata.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "name":  {
                                    "type":  "string"
                                },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address",
                     "name"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## set_addresses_names

Assign analyst names to multiple mapped addresses in one call and return per-address edit/readback success booleans. Hopper validates mapped destinations, native string representations, its 1024 UTF-16 code-unit symbol-name limit, and unique destinations/names before applying edits. Existing labels may move or swap only among explicitly selected addresses; unselected label owners remain unchanged. This mutates analysis metadata; verify failures individually.

```json
{
    "type":  "object",
    "properties":  {
                       "names":  {
                                     "type":  "object",
                                     "propertyNames":  {
                                                           "type":  "string"
                                                       },
                                     "additionalProperties":  {
                                                                  "type":  "string"
                                                              }
                                 },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "names"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## set_bookmark

Create or update a bookmark at a mapped hexadecimal address and report readback success. Omit name to preserve an existing name or create an unnamed bookmark; an empty string clears the bookmark name. This mutates navigation metadata; bookmarks are analyst-authored navigation aids, not binary evidence.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "name":  {
                                    "type":  "string"
                                },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## set_comment

Write a regular analysis comment at a hexadecimal address and return whether readback matched. An empty comment clears it. This mutates the Hopper document; use comments to record evidence IDs or reasoning.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "comment":  {
                                       "type":  "string"
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address",
                     "comment"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## set_inline_comment

Write an inline instruction comment at a hexadecimal address and return whether readback matched. An empty comment clears it; Hopper may flatten newlines, producing false on exact readback. This mutates analysis metadata.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "comment":  {
                                       "type":  "string"
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address",
                     "comment"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## start_adb_activity

Start an intent with `am start -W`: one action, optional data URI, optional explicit component, and up to 16 typed extras (string, boolean, int, long, float). A device-mutating tool; the full wait report determines success, with failed-attempt facts and raw output retained.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "description":  "Device serial exactly as `adb devices` reports it"
                                  },
                       "action":  {
                                      "type":  "string",
                                      "description":  "Intent action to start"
                                  },
                       "data_uri":  {
                                        "type":  "string",
                                        "description":  "Intent data URI (-d)"
                                    },
                       "component":  {
                                         "type":  "string",
                                         "description":  "Explicit component to start (-n)"
                                     },
                       "extras":  {
                                      "type":  "array",
                                      "items":  {
                                                    "type":  [
                                                                 "null",
                                                                 "boolean",
                                                                 "object",
                                                                 "array",
                                                                 "number",
                                                                 "string"
                                                             ],
                                                    "items":  {

                                                              }
                                                },
                                      "description":  "Typed intent extras (--es/--ez/--ei/--el/--ef)"
                                  }
                   },
    "required":  [
                     "serial",
                     "action"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## start_adb_app

Start one installed app's resolved launcher activity with `am start -W -n`. A device-mutating tool for dynamic analysis. The full wait report determines success; failures retain launch-attempt facts and raw output. Pair with `read_adb_logcat` (pid filter) and `list_adb_processes` to observe the app, and `stop_adb_app` to end the run.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "package":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "maxLength":  256,
                                       "pattern":  "^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)*$"
                                   }
                   },
    "required":  [
                     "serial",
                     "package"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## start_frida_session

Attach Frida to one selected process or spawn one selected target. Spawned targets remain paused until resume_frida_session, allowing scripts to load before execution. Closing a session detaches instrumentation; it does not kill the target.

```json
{
    "type":  "object",
    "properties":  {
                       "mode":  {
                                    "type":  "string",
                                    "enum":  [
                                                 "attach",
                                                 "spawn"
                                             ]
                                },
                       "device_id":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "remote":  {
                                      "type":  "object",
                                      "properties":  {
                                                         "address":  {
                                                                         "type":  "string",
                                                                         "minLength":  1,
                                                                         "maxLength":  2048
                                                                     },
                                                         "token":  {
                                                                       "type":  "string",
                                                                       "minLength":  1
                                                                   },
                                                         "certificate":  {
                                                                             "type":  "string",
                                                                             "minLength":  1
                                                                         },
                                                         "origin":  {
                                                                        "type":  "string",
                                                                        "minLength":  1
                                                                    },
                                                         "keepalive_interval":  {
                                                                                    "type":  "integer",
                                                                                    "exclusiveMinimum":  0,
                                                                                    "maximum":  9007199254740991
                                                                                }
                                                     },
                                      "required":  [
                                                       "address"
                                                   ],
                                      "additionalProperties":  false
                                  },
                       "pid":  {
                                   "type":  "integer",
                                   "exclusiveMinimum":  0,
                                   "maximum":  9007199254740991
                               },
                       "program":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "pattern":  "^[^\\x00]*$"
                                   },
                       "argv":  {
                                    "type":  "array",
                                    "items":  {
                                                  "type":  "string"
                                              }
                                }
                   },
    "required":  [
                     "mode"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## start_gdb_session

Start a BYO GDB process in MI3 mode when available, suppress startup files, and disable auto-loading. Persistent GDB sessions are available only on Linux, where REA can verify live inferior output descriptors through /proc before detaching; macOS descriptor verification is not implemented, and Windows Job Object cleanup can terminate debugger inferiors. Optionally set inferior_tty to a caller-selected terminal for future inferior runs; REA verifies the setting through GDB/MI before the session becomes ready. The selected terminal must remain available after GDB exits. A live inferior can be detached during close only while this durable output sink remains verified. The session persists in this REA MCP server process and receives an explicit session ID.

```json
{
    "type":  "object",
    "properties":  {
                       "inferior_tty":  {
                                            "type":  "string",
                                            "minLength":  1,
                                            "pattern":  "^[^\\x00]*$"
                                        }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## start_rizin_debug_session

Start a persistent Rizin debugger session using `-N` to suppress user settings and scripts while retaining plugins, `-0` for NUL framing, and `-d` with an optional caller-selected IO debugger backend. Requires a caller-selected Linux TTY for inferior stdout and stderr; the target may write directly to that terminal while running and after detach. REA verifies both destinations and `dbg.exitkills=false` before detaching. Backend and target capabilities depend on the installed build. The session lives in this REA MCP process. Persistent sessions require Linux `/proc` descriptor readback. In a shared MCP server, starting this session retires any active binary-analysis session; after target effects have settled, call open_binary before further analysis. Close an active Ghidra session explicitly first.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "inferior_tty":  {
                                            "type":  "string",
                                            "minLength":  1,
                                            "pattern":  "^[^\\x00]*$"
                                        },
                       "backend":  {
                                       "type":  "string",
                                       "minLength":  1
                                   }
                   },
    "required":  [
                     "path",
                     "inferior_tty"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## stop_adb_app

Force-stop every process of one package (`am force-stop`), ending a dynamic-analysis run. A device-mutating tool; it succeeds whether or not the package had running processes.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "package":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "maxLength":  256,
                                       "pattern":  "^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)*$"
                                   }
                   },
    "required":  [
                     "serial",
                     "package"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## trace_android_references

Trace incoming static using nodes to an exact Android class or selected method overload. The total counts containing nodes, not individual invoke instructions. Use inspect_android_class to choose an overload_index when names collide. Source lines and the containing node dex_offset remain unknown. direct_invokes carries exact DEX code-unit instruction offsets when available; direct_invokes_exhaustive false marks byte-bounded partial instruction evidence, and null means coverage is unknown. Using-node coverage remains separate; no runtime calls are inferred.

```json
{
    "type":  "object",
    "properties":  {
                       "path":  {
                                    "type":  "string",
                                    "minLength":  1,
                                    "pattern":  "^[^\\x00]*$"
                                },
                       "class_name":  {
                                          "type":  "string",
                                          "minLength":  1
                                      },
                       "method_name":  {
                                           "type":  "string",
                                           "minLength":  1
                                       },
                       "overload_index":  {
                                              "type":  "integer",
                                              "minimum":  0,
                                              "maximum":  9007199254740991
                                          }
                   },
    "required":  [
                     "path",
                     "class_name"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## trace_application_feature

Trace a typed literal seed through every reachable part of an authenticated JavaScript Application Graph supplied as inline Evidence or exact same-session retained references. Original static, native, passive-runtime, inferred, and unknown authorities remain distinct; native addon handoffs never open a provider or execute the application.

```json
{
    "type":  "object",
    "properties":  {
                       "native_observations":  {
                                                   "type":  "array",
                                                   "items":  {
                                                                 "type":  [
                                                                              "null",
                                                                              "boolean",
                                                                              "object",
                                                                              "array",
                                                                              "number",
                                                                              "string"
                                                                          ],
                                                                 "items":  {

                                                                           }
                                                             }
                                               },
                       "seed":  {
                                    "type":  "object"
                                },
                       "direction":  {
                                         "type":  "string",
                                         "description":  "Direction in which to traverse or compare relationships."
                                     },
                       "application":  {
                                           "type":  "object"
                                       }
                   },
    "required":  [
                     "seed",
                     "application"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## trace_call_path

Resolve start and optional goal symbols or addresses to canonical procedure entries, then trace direct callers or callees until the graph is exhausted or the goal is reached. Results use canonical addresses; Evidence preserves supplied selectors. Returns visited nodes, direct-call edges, a shortest traversal path, provider failures, and residual unknowns. A requested goal is unknown when unresolved expansions prevent proving it absent; unresolved indirect calls remain unknown.

```json
{
    "type":  "object",
    "properties":  {
                       "start":  {
                                     "type":  "string"
                                 },
                       "goal":  {
                                    "type":  "string"
                                },
                       "direction":  {
                                         "type":  "string",
                                         "enum":  [
                                                      "forward",
                                                      "backward"
                                                  ]
                                     }
                   },
    "required":  [
                     "start"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## trace_dylib_resolution

Trace how dyld would resolve each Mach-O dylib load for the active Mach-O, or for every executable in the active .app bundle (the main app, XPC services, extensions, login items, helpers). Expands @executable_path, @loader_path, and @rpath through each process's LC_RPATH stack in dependents-first load order, and reports every candidate path with its outcome, the resolved image and digest, weak, re-export, upward, and lazy-load flags, including LC_LAZY_LOAD_DYLIB_INFO linkedit payloads, and derived findings such as unresolved required loads or absent earlier @rpath candidates. Pure header parsing: nothing is executed, and absolute or system paths outside the analyzed root stay undetermined. Select roots or one architecture to narrow large bundles.

```json
{
    "type":  "object",
    "properties":  {
                       "roots":  {
                                     "minItems":  1,
                                     "type":  "array",
                                     "items":  {
                                                   "type":  "string",
                                                   "minLength":  1,
                                                   "pattern":  "^(?:[^\\x2f\\\\\\x00.][^\\x2f\\\\\\x00]*|\\.[^\\x2f\\\\\\x00.][^\\x2f\\\\\\x00]*|\\.\\.[^\\x2f\\\\\\x00]+)(?:\\x2f(?:[^\\x2f\\\\\\x00.][^\\x2f\\\\\\x00]*|\\.[^\\x2f\\\\\\x00.][^\\x2f\\\\\\x00]*|\\.\\.[^\\x2f\\\\\\x00]+))*$"
                                               }
                                 },
                       "architecture":  {
                                            "type":  "string",
                                            "enum":  [
                                                         "arm64",
                                                         "arm64e",
                                                         "x86_64"
                                                     ]
                                        }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## trace_feature

Trace the complete literal query, preserving spaces, through every matching string and procedure, their xrefs, and truthful containing-procedure resolution. Returns observations and residual unknowns without inferring reference kinds.

```json
{
    "type":  "object",
    "properties":  {
                       "query":  {
                                     "type":  "string",
                                     "minLength":  1
                                 },
                       "case_sensitive":  {
                                              "type":  "boolean"
                                          }
                   },
    "required":  [
                     "query"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## trace_javascript_semantics

Trace static JavaScript data-flow, direct call/return, and closure relations from inline application Evidence or an exact same-session retained reference. Dynamic or unsupported semantics remain explicit unknowns; static reachability never claims runtime execution.

```json
{
    "type":  "object",
    "properties":  {
                       "application":  {
                                           "type":  "object"
                                       },
                       "query":  {
                                     "type":  "object",
                                     "description":  "Non-empty feature or text query to investigate."
                                 }
                   },
    "required":  [
                     "application",
                     "query"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## trace_native_ui_action

Trace one unique compiled UI action, object ID, native symbol or exact function address through authored connections, encoded or symbolized Objective-C handlers, and bounded static call references. Typed direct and resolved indirect calls, ambiguous candidates, inferred untyped provider callees, and targetless sites remain distinct. Runtime reachability is unknown; use trace_native_values for recovered value dependencies.

```json
{
    "type":  "object",
    "properties":  {
                       "action":  {
                                      "type":  "string",
                                      "minLength":  1
                                  },
                       "max_depth":  {
                                         "type":  "integer",
                                         "minimum":  0,
                                         "maximum":  32
                                     },
                       "max_nodes":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  2000
                                     },
                       "max_edges":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  5000
                                     }
                   },
    "required":  [
                     "action"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## trace_native_values

Trace a bounded static dependency graph from one explicit native procedure. Includes high-p-code def-use, constants, operators, memory and branch operations, resolved call edges, and derived argument/parameter and return/output bindings. Missing bindings, alias semantics, persistent state and RNG roles remain unknown. Budgets bound depth, decompilations, graph size and payloads; nodes are paginated. A Ghidra per-function timeout does not bound the whole traversal; allow startup, every attempted function and other work, or reduce max_functions.

```json
{
    "type":  "object",
    "properties":  {
                       "procedure":  {
                                         "type":  "string",
                                         "minLength":  1
                                     },
                       "max_depth":  {
                                         "type":  "integer",
                                         "minimum":  0,
                                         "maximum":  16
                                     },
                       "max_functions":  {
                                             "type":  "integer",
                                             "minimum":  1,
                                             "maximum":  64
                                         },
                       "max_call_sites":  {
                                              "type":  "integer",
                                              "minimum":  1,
                                              "maximum":  4096
                                          },
                       "max_nodes":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  20000
                                     },
                       "max_edges":  {
                                         "type":  "integer",
                                         "minimum":  1,
                                         "maximum":  40000
                                     },
                       "offset":  {
                                      "type":  "integer",
                                      "minimum":  0,
                                      "maximum":  9007199254740991
                                  },
                       "limit":  {
                                     "type":  "integer",
                                     "minimum":  1,
                                     "maximum":  5000
                                 }
                   },
    "required":  [
                     "procedure"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## trace_web_module_imports

Trace one exported website script's outgoing native ES imports/re-exports, using exact URL and optional selected import-map context. Reads a local export_web_scripts manifest and verifies the selected source's SHA-256/size; returns source positions, native resolved URLs or exact errors, every matching captured candidate and explicit unknown execution. Query/fragment identities are preserved. Computed imports and bundler IDs remain unknown. Select importer_url for unknown inline/document-base context. Caller-supplied Chromium executes only a trusted REA resolver stub in an owned context with page requests locally fulfilled/blocked; no captured application execution or asset refetch. Requires absolute REA_BROWSER_EXECUTABLE for literal imports. 32 MiB manifest, 16 MiB source, 4 MiB map and 20-second native deadline; cleanup may extend the deadline.

```json
{
    "type":  "object",
    "properties":  {
                       "manifest_path":  {
                                             "type":  "string",
                                             "description":  "Absolute path to an export_web_scripts manifest"
                                         },
                       "script_index":  {
                                            "type":  "integer",
                                            "description":  "Zero-based index in manifest.scripts"
                                        },
                       "importer_url":  {
                                            "type":  "string",
                                            "description":  "Explicit HTTP(S) module URL; useful when captured inline/base context is unknown"
                                        },
                       "import_map":  {
                                          "type":  "object"
                                      }
                   },
    "required":  [
                     "manifest_path",
                     "script_index"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## trace_web_source_location

Trace one generated position in an exported website script through an explicitly paired local source map. Verifies manifest, selected script and map SHA-256/byte identities; returns every equal-position mapping, reported source/root, resolved source URL, embedded sourcesContent and original position inline. One-based lines and zero-based UTF-16 columns; greatest generated position <= requested across lines. Duplicate sources and generated-only mappings remain distinct. Caller-selected pairing does not establish deployment authenticity or execution. Regular maps and inline indexed sections only; external section URLs are unsupported. No network access or application execution; uses the pinned upstream codec in an owned Node process with private temporary files, independent 192 MiB old-generation heap and 20-second deadline. 4 MiB map, 32 MiB reply; complete evidence or actionable resource failure. Cleanup may extend the deadline.

```json
{
    "type":  "object",
    "properties":  {
                       "manifest_path":  {
                                             "type":  "string",
                                             "description":  "Absolute export_web_scripts manifest path"
                                         },
                       "script_index":  {
                                            "type":  "integer",
                                            "description":  "Zero-based index in manifest.scripts"
                                        },
                       "source_map":  {
                                          "type":  "object"
                                      },
                       "generated_position":  {
                                                  "type":  "object"
                                              },
                       "decode_timeout_ms":  {
                                                 "type":  "integer",
                                                 "description":  "Source-map decode deadline in milliseconds (default 20000), for the owned codec process. Omitted means 20000 and applies on the next trace_web_source_location call. deadlineExpired returns no partial mapping. Does not raise the fixed 4 MiB map budget, 32 MiB reply budget, or 192 MiB codec heap. Those have no runtime override."
                                             }
                   },
    "required":  [
                     "manifest_path",
                     "script_index",
                     "source_map",
                     "generated_position"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":true}

## uninstall_adb_package

Request package removal for user 0 (`adb shell pm uninstall --user 0`) without data retention. A device-mutating tool; absent packages report the device's own refusal reason.

```json
{
    "type":  "object",
    "properties":  {
                       "serial":  {
                                      "type":  "string",
                                      "minLength":  1,
                                      "maxLength":  256,
                                      "pattern":  "^[^\\x00\\s\"\u0027]+$"
                                  },
                       "package":  {
                                       "type":  "string",
                                       "minLength":  1,
                                       "maxLength":  256,
                                       "pattern":  "^[A-Za-z][A-Za-z0-9_]*(\\.[A-Za-z][A-Za-z0-9_]*)*$"
                                   }
                   },
    "required":  [
                     "serial",
                     "package"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":false,"openWorldHint":true}

## unload_frida_script

Unload one caller-selected Frida script from a live session. This removes that script's instrumentation from the target.

```json
{
    "type":  "object",
    "properties":  {
                       "session_id":  {
                                          "type":  "string",
                                          "format":  "uuid",
                                          "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                      },
                       "script_id":  {
                                         "type":  "string",
                                         "format":  "uuid",
                                         "pattern":  "^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|00000000-0000-0000-0000-000000000000|ffffffff-ffff-ffff-ffff-ffffffffffff)$"
                                     }
                   },
    "required":  [
                     "session_id",
                     "script_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":true}

## unset_bookmark

Remove the bookmark at a mapped hexadecimal address and return whether it is absent. Hopper also permits removing an existing legacy bookmark outside mapped memory. This mutates navigation metadata and does not alter binary bytes.

```json
{
    "type":  "object",
    "properties":  {
                       "address":  {
                                       "type":  "string"
                                   },
                       "document":  {
                                        "type":  "string"
                                    }
                   },
    "required":  [
                     "address"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":true,"idempotentHint":true,"openWorldHint":false}

## update_analysis_settings

When REA names an adjustable limit, atomically set or reset its runtime default, then retry once work and cleanup have settled. No inspection call is required when the error supplies the bounds. Changes apply only to new work, survive target close/open, and disappear on REA restart. Reset restores launch configuration. Does not edit files, restart providers, raise host limits, or override explicit per-call arguments.

```json
{
    "type":  "object",
    "properties":  {
                       "changes":  {
                                       "minItems":  1,
                                       "maxItems":  11,
                                       "type":  "array",
                                       "items":  {
                                                     "anyOf":  [
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "asar.max_header_bytes"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  268435456
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "ghidra.startup_timeout_ms"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  2147483647
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "hopper.startup_timeout_ms"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  2147483647
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "ghidra.decompile_timeout_seconds"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "anyOf":  [
                                                                                                                      {
                                                                                                                          "type":  "integer",
                                                                                                                          "minimum":  1,
                                                                                                                          "maximum":  2147483
                                                                                                                      },
                                                                                                                      {
                                                                                                                          "type":  "null"
                                                                                                                      }
                                                                                                                  ]
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "jadx.operation_timeout_ms"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  2147483647
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "regex.matching_deadline_ms"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  2147483647
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "regex.worker_heap_request_mib"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  7,
                                                                                                        "maximum":  16384
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "capture.decoder_old_generation_mib"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  8,
                                                                                                        "maximum":  16384
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "javascript.analysis_timeout_ms"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  2147483647
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "javascript.max_heap_mb"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  128,
                                                                                                        "maximum":  16384
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "const":  "wasm.command_timeout_ms"
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "set"
                                                                                                     },
                                                                                          "value":  {
                                                                                                        "type":  "integer",
                                                                                                        "minimum":  1,
                                                                                                        "maximum":  2147483647
                                                                                                    }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action",
                                                                                        "value"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   },
                                                                   {
                                                                       "type":  "object",
                                                                       "properties":  {
                                                                                          "setting":  {
                                                                                                          "type":  "string",
                                                                                                          "enum":  [
                                                                                                                       "asar.max_header_bytes",
                                                                                                                       "ghidra.startup_timeout_ms",
                                                                                                                       "hopper.startup_timeout_ms",
                                                                                                                       "ghidra.decompile_timeout_seconds",
                                                                                                                       "jadx.operation_timeout_ms",
                                                                                                                       "regex.matching_deadline_ms",
                                                                                                                       "regex.worker_heap_request_mib",
                                                                                                                       "capture.decoder_old_generation_mib",
                                                                                                                       "javascript.analysis_timeout_ms",
                                                                                                                       "javascript.max_heap_mb",
                                                                                                                       "wasm.command_timeout_ms"
                                                                                                                   ]
                                                                                                      },
                                                                                          "action":  {
                                                                                                         "type":  "string",
                                                                                                         "const":  "reset"
                                                                                                     }
                                                                                      },
                                                                       "required":  [
                                                                                        "setting",
                                                                                        "action"
                                                                                    ],
                                                                       "additionalProperties":  false
                                                                   }
                                                               ],
                                                     "type":  "object"
                                                 }
                                   },
                       "expected_revision":  {
                                                 "type":  "integer",
                                                 "minimum":  0,
                                                 "maximum":  9007199254740991
                                             }
                   },
    "required":  [
                     "changes"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## update_unknown

Append one immutable full-state revision and mutation evidence. Requires exact expected_revision; stale concurrent writers fail instead of overwriting newer analysis.

```json
{
    "type":  "object",
    "properties":  {
                       "unknown_id":  {
                                          "type":  "string",
                                          "description":  "Exact residual-unknown identifier."
                                      },
                       "expected_revision":  {
                                                 "type":  "integer",
                                                 "description":  "Expected revision used to reject stale or mismatched input."
                                             },
                       "status":  {
                                      "type":  "string",
                                      "description":  "Current lifecycle or verification status."
                                  },
                       "severity":  {
                                        "type":  "string"
                                    },
                       "supporting_evidence_ids":  {
                                                       "type":  "array",
                                                       "items":  {
                                                                     "type":  [
                                                                                  "null",
                                                                                  "boolean",
                                                                                  "object",
                                                                                  "array",
                                                                                  "number",
                                                                                  "string"
                                                                              ],
                                                                     "items":  {

                                                                               }
                                                                 },
                                                       "description":  "Ordered Evidence identifiers for the supporting observations."
                                                   },
                       "contradicting_evidence_ids":  {
                                                          "type":  "array",
                                                          "items":  {
                                                                        "type":  [
                                                                                     "null",
                                                                                     "boolean",
                                                                                     "object",
                                                                                     "array",
                                                                                     "number",
                                                                                     "string"
                                                                                 ],
                                                                        "items":  {

                                                                                  }
                                                                    },
                                                          "description":  "Ordered Evidence identifiers for the contradicting observations."
                                                      },
                       "required_authority":  {
                                                  "type":  [
                                                               "string",
                                                               "null"
                                                           ]
                                              },
                       "required_confidence":  {
                                                   "type":  "string"
                                               },
                       "required_environment":  {
                                                    "type":  [
                                                                 "object",
                                                                 "null"
                                                             ]
                                                },
                       "recommended_probes":  {
                                                  "type":  "array",
                                                  "items":  {
                                                                "type":  [
                                                                             "null",
                                                                             "boolean",
                                                                             "object",
                                                                             "array",
                                                                             "number",
                                                                             "string"
                                                                         ],
                                                                "items":  {

                                                                          }
                                                            }
                                              },
                       "relationships":  {
                                             "type":  "array",
                                             "items":  {
                                                           "type":  [
                                                                        "null",
                                                                        "boolean",
                                                                        "object",
                                                                        "array",
                                                                        "number",
                                                                        "string"
                                                                    ],
                                                           "items":  {

                                                                     }
                                                       }
                                         },
                       "resolution":  {
                                          "type":  [
                                                       "object",
                                                       "null"
                                                   ]
                                      }
                   },
    "required":  [
                     "unknown_id",
                     "expected_revision",
                     "status",
                     "severity",
                     "supporting_evidence_ids",
                     "contradicting_evidence_ids",
                     "required_authority",
                     "required_confidence",
                     "required_environment",
                     "recommended_probes",
                     "relationships",
                     "resolution"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":false,"openWorldHint":false}

## verify_managed_native_boundaries

Verify managed P/Invoke/native-boundary declarations against authenticated native export or function Evidence without executing managed code or translating managed metadata tokens into native addresses. The workflow preserves declaration-only, verified, inferred, contradicted, and unresolved states; a name candidate with missing managed or native module identity remains unresolved with its candidate evidence retained.

```json
{
    "type":  "object",
    "properties":  {
                       "managed_boundaries":  {
                                                  "type":  "object"
                                              },
                       "native_observations":  {
                                                   "type":  "array",
                                                   "items":  {
                                                                 "type":  [
                                                                              "null",
                                                                              "boolean",
                                                                              "object",
                                                                              "array",
                                                                              "number",
                                                                              "string"
                                                                          ],
                                                                 "items":  {

                                                                           }
                                                             },
                                                   "description":  "Native function or export Evidence records"
                                               }
                   },
    "required":  [
                     "managed_boundaries",
                     "native_observations"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## verify_reconstruction

Verify a finite typed behavioral and structural specification against a canonical Evidence bundle. Pass means every declared claim has complete comparable authority—not global source equivalence; changed claims fail and missing, limited, or unresolved evidence stays unknown.

```json
{
    "type":  "object",
    "properties":  {
                       "specification":  {
                                             "type":  "object"
                                         }
                   },
    "required":  [
                     "specification"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "description":  "Advertised in reduced form by the compact input schema profile; the server still validates complete canonical inputs.",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## verify_unknown_resolution

Revalidate the current residual-unknown head against live bundled evidence, exact authority/confidence/environment requirements, and revision integrity. Withdrawn and out-of-scope dispositions are not truth claims.

```json
{
    "type":  "object",
    "properties":  {
                       "unknown_id":  {
                                          "type":  "string",
                                          "pattern":  "^unk_[a-f0-9]{64}$"
                                      }
                   },
    "required":  [
                     "unknown_id"
                 ],
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":true,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}

## xrefs

Return analyzed references to a code or data address. Hopper may default to its current cursor; headless providers such as Ghidra require an explicit address. Use to connect strings, globals, selectors, and functions; bare addresses are untyped and indirect references may be incomplete. Use inspect_native_instruction at the target address for typed incoming references where supported.

```json
{
    "type":  "object",
    "properties":  {
                       "document":  {
                                        "type":  "string"
                                    },
                       "address":  {
                                       "type":  "string"
                                   }
                   },
    "$schema":  "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties":  false
}
```
annotations: {"readOnlyHint":false,"destructiveHint":false,"idempotentHint":true,"openWorldHint":false}
