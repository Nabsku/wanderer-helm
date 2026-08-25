{{/* Expand the chart name. */}}
{{- define "wanderer.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Expand the release-scoped name. */}}
{{- define "wanderer.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/* Chart label. */}}
{{- define "wanderer.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/* Common labels. */}}
{{- define "wanderer.labels" -}}
helm.sh/chart: {{ include "wanderer.chart" . }}
{{ include "wanderer.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/* Stable identity labels shared by all resources. */}}
{{- define "wanderer.selectorLabels" -}}
app.kubernetes.io/name: {{ include "wanderer.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/* Service account name. */}}
{{- define "wanderer.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "wanderer.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/* Image reference with optional immutable digest. */}}
{{- define "wanderer.image" -}}
{{- if .digest }}
{{- printf "%s@%s" .repository .digest }}
{{- else }}
{{- printf "%s:%s" .repository .tag }}
{{- end }}
{{- end }}

{{/* Secret name used by the chart. */}}
{{- define "wanderer.secretName" -}}
{{- if .Values.secret.existingSecret }}
{{- .Values.secret.existingSecret }}
{{- else if .Values.secret.name }}
{{- .Values.secret.name }}
{{- else }}
{{- printf "%s-secrets" (include "wanderer.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}

{{/* Internal service names. */}}
{{- define "wanderer.webServiceName" -}}
{{- printf "%s-web" (include "wanderer.fullname" .) }}
{{- end }}

{{- define "wanderer.databaseServiceName" -}}
{{- printf "%s-database" (include "wanderer.fullname" .) }}
{{- end }}

{{- define "wanderer.searchServiceName" -}}
{{- printf "%s-search" (include "wanderer.fullname" .) }}
{{- end }}

{{/* Optional Garmin synchronizer identity. */}}
{{- define "wanderer.garminSyncName" -}}
{{- printf "%s-garmin-sync" (include "wanderer.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "wanderer.garminSyncClaimName" -}}
{{- default (include "wanderer.garminSyncName" .) .Values.garminSync.persistence.existingClaim }}
{{- end }}

{{/* Internal API URL unless an operator supplies a separate URL. */}}
{{- define "wanderer.garminSyncWandererURL" -}}
{{- if .Values.garminSync.wanderer.url }}
{{- .Values.garminSync.wanderer.url }}
{{- else }}
{{- printf "http://%s:%d" (include "wanderer.webServiceName" .) (.Values.web.service.port | int) }}
{{- end }}
{{- end }}

{{/* Comma-separated sources consumed by the shared sync image. */}}
{{- define "wanderer.garminSyncSources" -}}
{{- $sources := list }}
{{- if .Values.garminSync.sources.garminConnect.enabled }}
{{- $sources = append $sources "garmin" }}
{{- end }}
{{- if .Values.garminSync.sources.officialExport.enabled }}
{{- $sources = append $sources "official" }}
{{- end }}
{{- join "," $sources }}
{{- end }}

{{/* Public origin is required because it controls CORS and federation URLs. */}}
{{- define "wanderer.origin" -}}
{{- required "web.origin must be set to the public Wanderer URL" .Values.web.origin }}
{{- end }}

{{/* Browser/server URL used to reach PocketBase. */}}
{{- define "wanderer.publicPocketbaseURL" -}}
{{- required "web.config.publicPocketbaseURL must be a URL reachable by both the browser and the web pod" .Values.web.config.publicPocketbaseURL }}
{{- end }}

{{/*
Generate secret data. lookup preserves generated values on upgrade. The
rendered Secret is only emitted when secret.existingSecret is empty.
*/}}
{{- define "wanderer.secretData" -}}
{{- $secretName := include "wanderer.secretName" . -}}
{{- $existing := lookup "v1" "Secret" .Release.Namespace $secretName -}}
{{- $pbKeyName := .Values.secret.keys.pocketbaseEncryption -}}
{{- $meiliKeyName := .Values.secret.keys.meiliMaster -}}
{{- $pbKey := .Values.secret.pocketbaseEncryptionKey | default "" -}}
{{- $meiliKey := .Values.secret.meiliMasterKey | default "" -}}
{{- if eq $pbKey "" }}
  {{- if and $existing $existing.data (hasKey $existing.data $pbKeyName) }}
    {{- $pbKey = (index $existing.data $pbKeyName | b64dec) -}}
  {{- else }}
    {{- $pbKey = randAlphaNum 32 -}}
  {{- end }}
{{- end }}
{{- if eq $meiliKey "" }}
  {{- if and $existing $existing.data (hasKey $existing.data $meiliKeyName) }}
    {{- $meiliKey = (index $existing.data $meiliKeyName | b64dec) -}}
  {{- else }}
    {{- $meiliKey = randAlphaNum 48 -}}
  {{- end }}
{{- end }}
{{ $pbKeyName }}: {{ $pbKey | b64enc | quote }}
{{ $meiliKeyName }}: {{ $meiliKey | b64enc | quote }}
{{- end }}

{{/* Pod restart checksum for chart-managed secret material. */}}
{{- define "wanderer.secretChecksum" -}}
{{- if .Values.secret.existingSecret }}
{{- printf "%s:%s:%s" .Values.secret.existingSecret .Values.secret.keys.pocketbaseEncryption .Values.secret.keys.meiliMaster | sha256sum }}
{{- else }}
{{- include "wanderer.secretData" . | sha256sum }}
{{- end }}
{{- end }}

{{/* PVC policy annotation. */}}
{{- define "wanderer.pvcAnnotations" -}}
{{- if .keep }}
helm.sh/resource-policy: keep
{{- end }}
{{- with .annotations }}
{{ toYaml . }}
{{- end }}
{{- end }}
