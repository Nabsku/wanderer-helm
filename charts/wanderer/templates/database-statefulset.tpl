apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {{ include "wanderer.databaseServiceName" . }}
  namespace: {{ .Release.Namespace }}
  labels:
    {{- include "wanderer.labels" . | nindent 4 }}
    app.kubernetes.io/component: database
spec:
  serviceName: {{ include "wanderer.databaseServiceName" . }}
  replicas: {{ .Values.database.replicaCount }}
  podManagementPolicy: OrderedReady
  updateStrategy:
    type: RollingUpdate
  selector:
    matchLabels:
      {{- include "wanderer.selectorLabels" . | nindent 6 }}
      app.kubernetes.io/component: database
  template:
    metadata:
      annotations:
        checksum/wanderer-secret: {{ include "wanderer.secretChecksum" . | quote }}
        {{- with .Values.database.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      labels:
        {{- include "wanderer.labels" . | nindent 8 }}
        app.kubernetes.io/component: database
        {{- with .Values.database.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      serviceAccountName: {{ include "wanderer.serviceAccountName" . }}
      automountServiceAccountToken: {{ .Values.serviceAccount.automountServiceAccountToken }}
      terminationGracePeriodSeconds: {{ .Values.database.terminationGracePeriodSeconds }}
      {{- with .Values.imagePullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.database.podSecurityContext }}
      securityContext:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.database.nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.database.affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.database.tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .Values.database.topologySpreadConstraints }}
      topologySpreadConstraints:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      initContainers:
        - name: wait-for-search
          image: curlimages/curl:8.18.0
          imagePullPolicy: IfNotPresent
          command: ["/bin/sh", "-ec"]
          args:
            - >-
              until curl --fail --silent --show-error --max-time 5
              http://{{ include "wanderer.searchServiceName" . }}:{{ .Values.search.service.port }}/health;
              do sleep 2; done
      containers:
        - name: database
          image: {{ include "wanderer.image" .Values.database.image | quote }}
          imagePullPolicy: {{ .Values.database.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.database.securityContext | nindent 12 }}
          ports:
            - name: http
              containerPort: 8090
              protocol: TCP
          env:
            - name: ORIGIN
              value: {{ include "wanderer.origin" . | quote }}
            - name: MEILI_URL
              value: {{ printf "http://%s:%d" (include "wanderer.searchServiceName" .) (.Values.search.service.port | int) | quote }}
            - name: MEILI_MASTER_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "wanderer.secretName" . }}
                  key: {{ .Values.secret.keys.meiliMaster }}
            - name: POCKETBASE_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: {{ include "wanderer.secretName" . }}
                  key: {{ .Values.secret.keys.pocketbaseEncryption }}
            - name: POCKETBASE_CRON_SYNC_SCHEDULE
              value: {{ .Values.database.config.cronSyncSchedule | quote }}
            {{- if .Values.database.config.smtp.enabled }}
            - name: POCKETBASE_SMTP_ENABLED
              value: "true"
            - name: POCKETBASE_SMTP_SENDER_ADDRESS
              value: {{ .Values.database.config.smtp.senderAddress | quote }}
            - name: POCKETBASE_SMTP_SENDER_NAME
              value: {{ .Values.database.config.smtp.senderName | quote }}
            - name: POCKETBASE_SMTP_HOST
              value: {{ .Values.database.config.smtp.host | quote }}
            - name: POCKETBASE_SMTP_PORT
              value: {{ .Values.database.config.smtp.port | quote }}
            - name: POCKETBASE_SMTP_USERNAME
              value: {{ .Values.database.config.smtp.username | quote }}
            {{- if .Values.database.config.smtp.password.existingSecret }}
            - name: POCKETBASE_SMTP_PASSWORD
              valueFrom:
                secretKeyRef:
                  name: {{ .Values.database.config.smtp.password.existingSecret }}
                  key: {{ .Values.database.config.smtp.password.key }}
            {{- end }}
            {{- end }}
            {{- with .Values.database.extraEnv }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
          {{- with .Values.database.extraEnvFrom }}
          envFrom:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          volumeMounts:
            - name: database-data
              mountPath: /pb_data
            - name: database-plugins
              mountPath: /data/plugins
            - name: tmp
              mountPath: /tmp
          {{- with .Values.database.startupProbe }}
          {{- if .enabled }}
          startupProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          {{- with .Values.database.livenessProbe }}
          {{- if .enabled }}
          livenessProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          {{- with .Values.database.readinessProbe }}
          {{- if .enabled }}
          readinessProbe:
            httpGet:
              path: {{ .path }}
              port: http
            periodSeconds: {{ .periodSeconds }}
            timeoutSeconds: {{ .timeoutSeconds }}
            failureThreshold: {{ .failureThreshold }}
          {{- end }}
          {{- end }}
          resources:
            {{- toYaml .Values.database.resources | nindent 12 }}
      volumes:
        - name: database-data
          {{- if .Values.database.persistence.data.enabled }}
          persistentVolumeClaim:
            claimName: {{ default (printf "%s-database-data" (include "wanderer.fullname" .)) .Values.database.persistence.data.existingClaim }}
          {{- else }}
          emptyDir: {}
          {{- end }}
        - name: database-plugins
          {{- if .Values.database.persistence.plugins.enabled }}
          persistentVolumeClaim:
            claimName: {{ default (printf "%s-database-plugins" (include "wanderer.fullname" .)) .Values.database.persistence.plugins.existingClaim }}
          {{- else }}
          emptyDir: {}
          {{- end }}
        - name: tmp
          emptyDir: {}
