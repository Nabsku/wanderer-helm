{{- if .Values.garminSync.enabled }}
{{- if not (or .Values.garminSync.sources.garminConnect.enabled .Values.garminSync.sources.officialExport.enabled) }}
{{- fail "garminSync.enabled requires at least one enabled source" }}
{{- end }}
{{- if not (or .Values.garminSync.persistence.enabled .Values.garminSync.persistence.existingClaim) }}
{{- fail "garminSync requires persistence.enabled or persistence.existingClaim" }}
{{- end }}
{{- if not .Values.garminSync.wanderer.existingSecret }}
{{- fail "garminSync.wanderer.existingSecret must reference a Secret containing the Wanderer API token" }}
{{- end }}
{{- if and .Values.networkPolicy.enabled .Values.garminSync.sources.garminConnect.enabled (not .Values.networkPolicy.garminSyncEgress) }}
{{- fail "networkPolicy.garminSyncEgress is required when Garmin sync and NetworkPolicy are enabled" }}
{{- end }}
{{- if and .Values.garminSync.sources.garminConnect.enabled (not .Values.garminSync.sources.garminConnect.secret.existingSecret) }}
{{- fail "garminSync.sources.garminConnect.secret.existingSecret is required when Garmin Connect sync is enabled" }}
{{- end }}
apiVersion: batch/v1
kind: CronJob
metadata:
  name: {{ include "wanderer.garminSyncName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: garmin-sync
spec:
  schedule: {{ .Values.garminSync.schedule | quote }}
  suspend: {{ .Values.garminSync.suspend }}
  concurrencyPolicy: {{ .Values.garminSync.concurrencyPolicy }}
  startingDeadlineSeconds: {{ .Values.garminSync.startingDeadlineSeconds }}
  successfulJobsHistoryLimit: {{ .Values.garminSync.successfulJobsHistoryLimit }}
  failedJobsHistoryLimit: {{ .Values.garminSync.failedJobsHistoryLimit }}
  jobTemplate:
    metadata:
      labels:
        {{- include "wanderer.labels" . | nindent 8 }}
        app.kubernetes.io/component: garmin-sync
    spec:
      backoffLimit: {{ .Values.garminSync.backoffLimit }}
      activeDeadlineSeconds: {{ .Values.garminSync.activeDeadlineSeconds }}
      ttlSecondsAfterFinished: {{ .Values.garminSync.ttlSecondsAfterFinished }}
      template:
        metadata:
          annotations:
            {{- with .Values.garminSync.podAnnotations }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          labels:
            {{- include "wanderer.labels" . | nindent 12 }}
            app.kubernetes.io/component: garmin-sync
            {{- with .Values.garminSync.podLabels }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
        spec:
          automountServiceAccountToken: false
          restartPolicy: Never
          terminationGracePeriodSeconds: {{ .Values.garminSync.terminationGracePeriodSeconds }}
          {{- with .Values.imagePullSecrets }}
          imagePullSecrets:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          securityContext:
            {{- toYaml .Values.garminSync.podSecurityContext | nindent 12 }}
          {{- with .Values.garminSync.nodeSelector }}
          nodeSelector:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.garminSync.affinity }}
          affinity:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.garminSync.tolerations }}
          tolerations:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .Values.garminSync.topologySpreadConstraints }}
          topologySpreadConstraints:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          containers:
            - name: garmin-sync
              image: {{ include "wanderer.image" .Values.garminSync.image | quote }}
              imagePullPolicy: {{ .Values.garminSync.image.pullPolicy }}
              securityContext:
                {{- toYaml .Values.garminSync.securityContext | nindent 16 }}
              env:
                - name: SYNC_SOURCES
                  value: {{ include "wanderer.garminSyncSources" . | quote }}
                - name: WANDERER_URL
                  value: {{ include "wanderer.garminSyncWandererURL" . | quote }}
                - name: WANDERER_API_TOKEN
                  valueFrom:
                    secretKeyRef:
                      name: {{ .Values.garminSync.wanderer.existingSecret }}
                      key: {{ .Values.garminSync.wanderer.tokenKey }}
                - name: SYNC_DATA_DIR
                  value: /data
                - name: SYNC_INBOX_DIR
                  value: /data/inbox
                - name: SYNC_ARCHIVE_DIR
                  value: /data/archive
                - name: SYNC_MANIFEST
                  value: /data/state/manifest.json
                - name: GARMINTOKENS
                  value: /data/state/garmin_tokens.json
                - name: FIT_MODE
                  value: {{ .Values.garminSync.fitMode | quote }}
                - name: GARMIN_DOWNLOAD_FORMAT
                  value: {{ .Values.garminSync.sources.garminConnect.downloadFormat | quote }}
                - name: GARMIN_PAGE_SIZE
                  value: {{ .Values.garminSync.sources.garminConnect.pageSize | quote }}
                - name: GARMIN_MAX_PAGES
                  value: {{ .Values.garminSync.sources.garminConnect.maxPages | quote }}
                - name: MAX_FILE_BYTES
                  value: {{ printf "%d" (int64 .Values.garminSync.limits.maxFileBytes) | quote }}
                - name: MAX_ZIP_MEMBERS
                  value: {{ printf "%d" (int64 .Values.garminSync.limits.maxZipMembers) | quote }}
                - name: MAX_ZIP_UNCOMPRESSED_BYTES
                  value: {{ printf "%d" (int64 .Values.garminSync.limits.maxZipUncompressedBytes) | quote }}
                - name: MAX_PHOTOS_PER_ACTIVITY
                  value: {{ printf "%d" (int64 .Values.garminSync.limits.maxPhotosPerActivity) | quote }}
                - name: REQUEST_TIMEOUT_SECONDS
                  value: {{ .Values.garminSync.requestTimeoutSeconds | quote }}
                - name: UPLOAD_RETRIES
                  value: {{ .Values.garminSync.uploadRetries | quote }}
                - name: RETRY_BACKOFF_SECONDS
                  value: {{ .Values.garminSync.retryBackoffSeconds | quote }}
                - name: RETRY_MAX_BACKOFF_SECONDS
                  value: {{ .Values.garminSync.retryMaxBackoffSeconds | quote }}
                {{- if .Values.garminSync.sources.garminConnect.enabled }}
                - name: GARMIN_EMAIL
                  valueFrom:
                    secretKeyRef:
                      name: {{ .Values.garminSync.sources.garminConnect.secret.existingSecret }}
                      key: {{ .Values.garminSync.sources.garminConnect.secret.emailKey }}
                - name: GARMIN_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: {{ .Values.garminSync.sources.garminConnect.secret.existingSecret }}
                      key: {{ .Values.garminSync.sources.garminConnect.secret.passwordKey }}
                {{- end }}
              volumeMounts:
                - name: sync-data
                  mountPath: /data
                - name: tmp
                  mountPath: /tmp
              resources:
                {{- toYaml .Values.garminSync.resources | nindent 16 }}
          volumes:
            - name: sync-data
              persistentVolumeClaim:
                claimName: {{ include "wanderer.garminSyncClaimName" . }}
            - name: tmp
              emptyDir: {}
{{- end }}
