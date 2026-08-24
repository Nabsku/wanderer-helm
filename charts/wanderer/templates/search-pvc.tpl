{{- if and .Values.search.persistence.enabled (not .Values.search.persistence.existingClaim) }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "wanderer.fullname" . }}-search-data
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: search
  annotations:
    {{- include "wanderer.pvcAnnotations" .Values.search.persistence | nindent 4 }}
spec:
  accessModes:
    {{- toYaml .Values.search.persistence.accessModes | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.search.persistence.size }}
  {{- with .Values.search.persistence.storageClass }}
  storageClassName: {{ . | quote }}
  {{- end }}
{{- end }}
