{{- if and .Values.garminSync.enabled .Values.garminSync.persistence.enabled (not .Values.garminSync.persistence.existingClaim) }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "wanderer.garminSyncClaimName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: garmin-sync
  annotations:
    {{- include "wanderer.pvcAnnotations" .Values.garminSync.persistence | nindent 4 }}
spec:
  accessModes:
    {{- toYaml .Values.garminSync.persistence.accessModes | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.garminSync.persistence.size }}
  {{- with .Values.garminSync.persistence.storageClass }}
  storageClassName: {{ . | quote }}
  {{- end }}
{{- end }}
