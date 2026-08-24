{{- if and .Values.web.persistence.enabled (not .Values.web.persistence.existingClaim) }}
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {{ include "wanderer.fullname" . }}-web-uploads
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: web
  annotations:
    {{- include "wanderer.pvcAnnotations" .Values.web.persistence | nindent 4 }}
spec:
  accessModes:
    {{- toYaml .Values.web.persistence.accessModes | nindent 4 }}
  resources:
    requests:
      storage: {{ .Values.web.persistence.size }}
  {{- with .Values.web.persistence.storageClass }}
  storageClassName: {{ . | quote }}
  {{- end }}
{{- end }}
