apiVersion: v1
kind: Service
metadata:
  name: {{ include "wanderer.databaseServiceName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: database
  {{- with .Values.database.service.annotations }}
  annotations:
    {{- toYaml . | nindent 4 }}
  {{- end }}
spec:
  type: ClusterIP
  ports:
    - name: http
      port: {{ .Values.database.service.port }}
      targetPort: http
      protocol: TCP
  selector:
    {{- include "wanderer.selectorLabels" . | nindent 4 }}
    app.kubernetes.io/component: database
