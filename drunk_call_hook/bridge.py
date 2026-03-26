"""
CallBridge - Python ↔ Go gRPC bridge

Responsibilities:
- gRPC client for bidirectional communication with Go service
- Session lifecycle management
- Event streaming from Go → Python

Go service process is managed by MainWindow (app-level resource).
Each account creates its own CallBridge instance (gRPC client).
"""

import asyncio
import logging
import subprocess
import os
import platform
import threading
from pathlib import Path
from typing import Optional, Dict, Any, Callable

import grpc
from .proto import call_pb2, call_pb2_grpc

# Import paths utility for proper log directory handling (dev + XDG modes)
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from siproxylin.utils.paths import get_paths, PATH_MODE


class GoCallService:
    """
    Manages the Go service process lifecycle.

    This is an app-level singleton owned by MainWindow.
    Multiple CallBridge instances (one per account) connect to this service.
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        """
        Initialize Go service manager.

        Args:
            logger: Logger instance
        """
        self.logger = logger or logging.getLogger(__name__)
        self._process: Optional[subprocess.Popen] = None
        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._heartbeat_stop_event = threading.Event()
        self._grpc_channel: Optional[grpc.Channel] = None  # Synchronous channel for heartbeat thread

    async def start(self) -> bool:
        """
        Start Go service process.

        Returns:
            True if started successfully
        """
        if self._running:
            self.logger.warning("Go service already running")
            return True

        try:
            # Find Go binary
            binary_path = self._find_go_binary()
            if not binary_path:
                self.logger.error("Go service binary not found")
                return False

            # Start Go process with proper logging
            # Use proper path system (respects both dev mode and XDG mode)
            paths = get_paths()
            go_log_file = paths.call_service_log_path()
            go_stdout_file = paths.call_service_stdout_log_path()
            go_err_file = paths.call_service_stderr_log_path()

            self.logger.info(f"Starting Go service: {binary_path}")
            self.logger.info(f"Go logs -> {go_log_file}")
            self.logger.info(f"Go stdout (libnice) -> {go_stdout_file}")
            self.logger.info(f"Go stderr (GStreamer/panics) -> {go_err_file}")

            # Redirect stderr for GStreamer debug output and panics/crashes
            # Go writes structured logs to file via -log-path
            stderr_file = open(go_err_file, 'w')

            # Enable GStreamer debug logging for webrtcbin
            import os
            import json
            env = os.environ.copy()

            # Load GStreamer debug settings from config file
            gstreamer_config_path = get_paths().config_dir / 'gstreamer.json'
            gstreamer_settings = {}

            if gstreamer_config_path.exists():
                try:
                    with open(gstreamer_config_path, 'r') as f:
                        gstreamer_settings = json.load(f)
                    self.logger.info(f"Loaded GStreamer debug settings from {gstreamer_config_path}")
                except Exception as e:
                    self.logger.warning(f"Failed to load GStreamer settings from {gstreamer_config_path}: {e}")

            # Apply settings (empty by default - user must configure if needed)
            env['GST_DEBUG'] = gstreamer_settings.get('GST_DEBUG', '')
            env['G_MESSAGES_DEBUG'] = gstreamer_settings.get('G_MESSAGES_DEBUG', '')
            env['NICE_DEBUG'] = gstreamer_settings.get('NICE_DEBUG', '')

            self.logger.info(f"GStreamer debug settings: GST_DEBUG={env['GST_DEBUG']}")
            self.logger.info(f"GStreamer debug settings: G_MESSAGES_DEBUG={env['G_MESSAGES_DEBUG']}")
            self.logger.info(f"GStreamer debug settings: NICE_DEBUG={env['NICE_DEBUG']}")

            # Windows-specific: Add bundled GStreamer and vcpkg DLLs to PATH
            if platform.system() == "Windows":
                # Get absolute path to project root (relative to this file)
                bridge_dir = Path(__file__).parent  # drunk_call_hook/
                project_root = bridge_dir.parent     # siproxylin/

                # Bundled dependency paths
                gstreamer_bin = project_root / "drunk_call_service" / "lib" / "gstreamer" / "bin"
                gstreamer_plugins = project_root / "drunk_call_service" / "lib" / "gstreamer" / "lib" / "gstreamer-1.0"
                service_bin = project_root / "drunk_call_service" / "bin"

                # Build PATH with components that exist
                path_components = []
                existing_path = env.get("PATH", "")

                # Check bundled GStreamer
                if gstreamer_bin.exists():
                    path_components.append(str(gstreamer_bin))
                    self.logger.info(f"Found bundled GStreamer at: {gstreamer_bin}")

                    # Set GStreamer plugin path
                    if gstreamer_plugins.exists():
                        env["GST_PLUGIN_PATH"] = str(gstreamer_plugins)
                        env["GST_PLUGIN_SYSTEM_PATH"] = str(gstreamer_plugins)
                        self.logger.info(f"GStreamer plugin path: {gstreamer_plugins}")
                    else:
                        self.logger.warning(f"GStreamer plugins not found at {gstreamer_plugins}")
                else:
                    self.logger.error(f"Bundled GStreamer not found at {gstreamer_bin}")
                    self.logger.error("Download siproxylin-windows-gst-deps-vX.zip from GitHub releases")
                    self.logger.error("Extract to drunk_call_service/lib/gstreamer/")

                # Add vcpkg DLLs (should always exist after build)
                if service_bin.exists():
                    path_components.append(str(service_bin.resolve()))
                    self.logger.info(f"Found vcpkg DLLs and service binary at: {service_bin}")
                else:
                    self.logger.error(f"Service binaries not found at {service_bin}")
                    self.logger.error("Build with: cd drunk_call_service && make winrel")

                # Update PATH if we have components to add
                if path_components:
                    env["PATH"] = ";".join(path_components) + ";" + existing_path
                    self.logger.debug(f"Windows PATH updated: {env['PATH']}")
                else:
                    self.logger.error("No valid paths to add - call service will not start")

            # Load log level from logging settings
            logging_config_path = paths.config_dir / 'logging.json'
            log_level = 'INFO'  # Default
            if logging_config_path.exists():
                try:
                    with open(logging_config_path, 'r') as f:
                        logging_settings = json.load(f)
                        log_level = logging_settings.get('call_service_log_level', 'INFO')
                    self.logger.info(f"Loaded call service log level from {logging_config_path}: {log_level}")
                except Exception as e:
                    self.logger.warning(f"Failed to load logging settings from {logging_config_path}: {e}")

            stdout_file = open(go_stdout_file, 'a')

            self._process = subprocess.Popen(
                [binary_path, "--log-level", log_level, "--log-path", str(go_log_file)],
                stdout=stdout_file,  # Capture libnice debug output!
                stderr=stderr_file,
                env=env,
            )

            # Wait for service to be ready (health check)
            await self._wait_for_ready(timeout=5.0)

            self._running = True

            # Start heartbeat to keep Go service alive
            await self._start_heartbeat()

            return True

        except Exception as e:
            self.logger.error(f"Failed to start Go service: {e}")
            await self.stop()
            return False

    async def stop(self):
        """Stop Go service process gracefully."""
        if not self._running:
            return

        self.logger.info("Stopping Go service")

        # Stop heartbeat thread first
        await self._stop_heartbeat()

        # Send graceful shutdown RPC (Go will exit immediately)
        # Create temporary async channel for shutdown RPC
        shutdown_channel = None
        try:
            shutdown_channel = grpc.aio.insecure_channel('localhost:50051')
            stub = call_pb2_grpc.CallServiceStub(shutdown_channel)
            await stub.Shutdown(call_pb2.Empty())
            self.logger.info("Sent Shutdown RPC to Go service")
        except Exception as e:
            self.logger.warning(f"Failed to send Shutdown RPC: {e}")
        finally:
            if shutdown_channel:
                await shutdown_channel.close()

        # Close synchronous gRPC channel (used by heartbeat thread)
        if self._grpc_channel:
            self._grpc_channel.close()
            self._grpc_channel = None

        # Fallback: Terminate Go process if still running
        if self._process:
            try:
                self._process.wait(timeout=1.0)  # Wait briefly for graceful exit
            except subprocess.TimeoutExpired:
                self.logger.warning("Go service didn't stop after Shutdown RPC, terminating")
                self._process.terminate()
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    self.logger.warning("Go service didn't stop gracefully, killing")
                    self._process.kill()
            self._process = None

        self._running = False
        self.logger.info("Go service stopped")

    def _find_go_binary(self) -> Optional[str]:
        """
        Find Go service binary for current platform.

        Returns:
            Path to binary or None
        """
        system = platform.system().lower()
        binary_name = f"drunk-call-service-{system}"
        if system == "windows":
            binary_name += ".exe"

        # Check if running in production mode (AppImage or installed)
        if PATH_MODE != 'dev':
            # XDG mode: Look in /usr/local/bin (or $APPDIR/usr/local/bin in AppImage)
            appdir = os.getenv('APPDIR', '')
            if appdir:
                binary_path = Path(appdir) / "usr" / "local" / "bin" / binary_name
            else:
                binary_path = Path("/usr/local/bin") / binary_name

            if binary_path.exists():
                return str(binary_path)

        # Development mode: Look in project directory
        project_root = Path(__file__).parent.parent
        binary_path = project_root / "drunk_call_service" / "bin" / binary_name

        if binary_path.exists():
            return str(binary_path)

        # Fallback: Look in same directory as this module
        binary_path = Path(__file__).parent / binary_name
        if binary_path.exists():
            return str(binary_path)

        return None

    async def _wait_for_ready(self, timeout: float = 5.0):
        """
        Wait for Go service to be ready.

        Args:
            timeout: Max seconds to wait

        Raises:
            TimeoutError: If service doesn't become ready
        """
        # Wait for gRPC server to be listening
        start_time = asyncio.get_event_loop().time()

        while (asyncio.get_event_loop().time() - start_time) < timeout:
            try:
                # Try to connect
                channel = grpc.aio.insecure_channel('localhost:50051')
                # Simple connectivity check
                await channel.channel_ready()
                await channel.close()
                self.logger.debug("Go service is ready")
                return
            except Exception:
                await asyncio.sleep(0.1)

        raise TimeoutError("Go service did not become ready in time")

    async def _start_heartbeat(self):
        """Start heartbeat thread to keep Go service alive (runs independently of asyncio event loop)."""
        # Create synchronous gRPC channel for heartbeat thread
        self._grpc_channel = grpc.insecure_channel('localhost:50051')

        # Wait for synchronous channel to be ready (avoid race condition with Go startup)
        try:
            grpc.channel_ready_future(self._grpc_channel).result(timeout=5.0)
        except grpc.FutureTimeoutError:
            self.logger.error("Heartbeat channel failed to connect to Go service")
            return

        # Clear stop event
        self._heartbeat_stop_event.clear()

        # Start heartbeat thread (daemon=True so it doesn't block shutdown)
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            daemon=True,
            name="GoCallServiceHeartbeat"
        )
        self._heartbeat_thread.start()
        self.logger.info("Heartbeat thread started (5s interval, independent of GUI event loop)")

    async def _stop_heartbeat(self):
        """Stop heartbeat thread."""
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            # Signal thread to stop
            self._heartbeat_stop_event.set()

            # Wait for thread to finish (with timeout)
            self._heartbeat_thread.join(timeout=2.0)

            if self._heartbeat_thread.is_alive():
                self.logger.warning("Heartbeat thread did not stop gracefully")
            else:
                self.logger.info("Heartbeat thread stopped")

            self._heartbeat_thread = None

    def _heartbeat_loop(self):
        """
        Send heartbeat to Go service every 5 seconds (runs in separate thread).

        This runs independently of the asyncio event loop, ensuring heartbeats
        continue even if the GUI thread is blocked by heavy operations (e.g., DB queries).
        """
        try:
            stub = call_pb2_grpc.CallServiceStub(self._grpc_channel)

            self.logger.debug("Heartbeat loop started in thread")

            while not self._heartbeat_stop_event.is_set():
                try:
                    # Synchronous gRPC call (not async)
                    stub.Heartbeat(call_pb2.Empty())
                    self.logger.debug("Heartbeat sent")
                except Exception as e:
                    self.logger.warning(f"Heartbeat failed: {e}")

                # Wait 5 seconds or until stop event is set (whichever comes first)
                self._heartbeat_stop_event.wait(timeout=5.0)

            self.logger.debug("Heartbeat loop exiting")

        except Exception as e:
            self.logger.error(f"Heartbeat loop crashed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())


class CallBridge:
    """
    gRPC client for communicating with Go call service.

    Each XMPP account creates its own CallBridge instance.
    All instances connect to the same Go service (managed by MainWindow).

    Usage:
        bridge = CallBridge(
            on_ice_candidate=lambda sid, cand: ...,
            on_connection_state=lambda sid, state: ...
        )
        await bridge.connect()

        session_id = await bridge.create_session(peer_jid)
        sdp = await bridge.create_offer(session_id)
        await bridge.add_ice_candidate(session_id, candidate)

        await bridge.disconnect()
    """

    def __init__(self,
                 logger: Optional[logging.Logger] = None,
                 on_ice_candidate: Optional[Callable] = None,
                 on_connection_state: Optional[Callable] = None):
        """
        Initialize CallBridge gRPC client.

        Args:
            logger: Logger instance
            on_ice_candidate: Callback for ICE candidates from Go (session_id, candidate_dict)
            on_connection_state: Callback for connection state changes (session_id, state_str)
        """
        self.logger = logger or logging.getLogger(__name__)
        self.on_ice_candidate = on_ice_candidate
        self.on_connection_state = on_connection_state

        self._grpc_channel: Optional[grpc.aio.Channel] = None
        self._stub: Optional[call_pb2_grpc.CallServiceStub] = None
        self._connected = False

        # Event streaming tasks per session
        self._event_streams: Dict[str, asyncio.Task] = {}
        self._stream_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """
        Connect to Go service via gRPC.

        Go service must already be running (started by MainWindow).

        Returns:
            True if connected successfully
        """
        if self._connected:
            self.logger.warning("CallBridge already connected")
            return True

        try:
            # Connect gRPC client
            self._grpc_channel = grpc.aio.insecure_channel('localhost:50051')
            self._stub = call_pb2_grpc.CallServiceStub(self._grpc_channel)

            # Test connection
            await self._grpc_channel.channel_ready()

            self._connected = True
            self.logger.info("CallBridge connected to Go service")
            return True

        except Exception as e:
            self.logger.error(f"Failed to connect CallBridge: {e}")
            await self.disconnect()
            return False

    async def disconnect(self):
        """Disconnect from Go service and cleanup."""
        if not self._connected:
            return

        self.logger.info("Disconnecting CallBridge")

        # Cancel all event streams
        async with self._stream_lock:
            for session_id, task in self._event_streams.items():
                if not task.done():
                    self.logger.debug(f"Cancelling event stream for {session_id}")
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            self._event_streams.clear()

        # Close gRPC channel
        if self._grpc_channel:
            await self._grpc_channel.close()
            self._grpc_channel = None
            self._stub = None

        self._connected = False
        self.logger.info("CallBridge disconnected")

    async def create_session(self, peer_jid: str, session_id: str,
                             microphone_device: str = "", speakers_device: str = "",
                             microphone_display_name: str = "", speakers_display_name: str = "",
                             proxy_host: str = "", proxy_port: int = 0,
                             proxy_username: str = "", proxy_password: str = "",
                             proxy_type: str = "",
                             turn_server: str = "", turn_username: str = "",
                             turn_password: str = "",
                             echo_cancel: bool = True, echo_suppression_level: int = 1,
                             noise_suppression: bool = True, noise_suppression_level: int = 1,
                             gain_control: bool = True) -> bool:
        """
        Create new call session.

        Args:
            peer_jid: Remote peer JID
            session_id: Jingle session ID
            microphone_device: Microphone device ID (empty = default)
            speakers_device: Speakers device ID (empty = default)
            microphone_display_name: Human-readable microphone name (for logging only)
            speakers_display_name: Human-readable speakers name (for logging only)
            proxy_host: Proxy hostname/IP (empty = no proxy)
            proxy_port: Proxy port (e.g., 9050 for Tor, 3128 for HTTP)
            proxy_username: Proxy authentication username (optional)
            proxy_password: Proxy authentication password (optional)
            proxy_type: "SOCKS5" or "HTTP" (empty = no proxy)
            turn_server: TURN server URL (e.g., "turn:turn.example.com:3478"), empty = use Jami default
            turn_username: TURN authentication username (optional, from XEP-0215)
            turn_password: TURN authentication password (optional, from XEP-0215)
            echo_cancel: Enable echo cancellation (default: True)
            echo_suppression_level: Echo suppression level 0=low, 1=moderate, 2=high (default: 1)
            noise_suppression: Enable noise suppression (default: True)
            noise_suppression_level: Noise suppression level 0-3 (low/moderate/high/very-high, default: 1)
            gain_control: Enable automatic gain control (default: True)

        Returns:
            True if session created
        """
        if not self._stub:
            self.logger.error("gRPC stub not initialized")
            return False

        # Format device labels for logging (show both display name and ID for disambiguation)
        if microphone_display_name and microphone_device:
            mic_label = f"{microphone_display_name} ({microphone_device})"
        elif microphone_device:
            mic_label = microphone_device
        else:
            mic_label = "default"

        if speakers_display_name and speakers_device:
            speakers_label = f"{speakers_display_name} ({speakers_device})"
        elif speakers_device:
            speakers_label = speakers_device
        else:
            speakers_label = "default"

        # Log proxy settings (mask password for security)
        proxy_info = f"no proxy"
        if proxy_host and proxy_type:
            proxy_info = f"{proxy_type} proxy at {proxy_host}:{proxy_port}"
            if proxy_username:
                proxy_info += f" (auth: {proxy_username})"

        # Log TURN settings (mask password for security)
        turn_info = "Jami default"
        if turn_server:
            turn_info = f"{turn_server}"
            if turn_username:
                turn_info += f" (user: {turn_username})"

        self.logger.info(f"Creating session {session_id} with {peer_jid}, mic={mic_label}, speakers={speakers_label}, {proxy_info}, TURN={turn_info}")

        request = call_pb2.CreateSessionRequest(
            session_id=session_id,
            peer_jid=peer_jid,
            microphone_device=microphone_device,
            speakers_device=speakers_device,
            proxy_host=proxy_host,
            proxy_port=proxy_port,
            proxy_username=proxy_username,
            proxy_password=proxy_password,
            proxy_type=proxy_type,
            turn_server=turn_server,
            turn_username=turn_username,
            turn_password=turn_password,
            relay_only=True,  # Privacy: Force relay-only mode to prevent IP leaks
            echo_cancel=echo_cancel,
            echo_suppression_level=echo_suppression_level,
            noise_suppression=noise_suppression,
            noise_suppression_level=noise_suppression_level,
            gain_control=gain_control
        )

        response = await self._stub.CreateSession(request)

        if response.success:
            self.logger.info(f"Session {session_id} created successfully")

            # Start event streaming for this session
            await self._start_event_stream(session_id)

            return True
        else:
            self.logger.error(f"Failed to create session: {response.error}")
            return False

    async def create_offer(self, session_id: str) -> str:
        """
        Create SDP offer for session.

        Args:
            session_id: Session ID

        Returns:
            SDP offer string
        """
        if not self._stub:
            raise RuntimeError("gRPC stub not initialized")

        self.logger.debug(f"Creating offer for session {session_id}")

        request = call_pb2.CreateOfferRequest(session_id=session_id)
        response = await self._stub.CreateOffer(request)

        if response.error:
            raise RuntimeError(f"Failed to create offer: {response.error}")

        self.logger.debug(f"Offer created for session {session_id}")
        return response.sdp

    async def create_answer(self, session_id: str, remote_sdp: str) -> str:
        """
        Create SDP answer for incoming session.

        Args:
            session_id: Session ID
            remote_sdp: Remote SDP offer

        Returns:
            SDP answer string
        """
        if not self._stub:
            raise RuntimeError("gRPC stub not initialized")

        self.logger.debug(f"Creating answer for session {session_id}")

        request = call_pb2.CreateAnswerRequest(
            session_id=session_id,
            remote_sdp=remote_sdp
        )
        response = await self._stub.CreateAnswer(request)

        if response.error:
            raise RuntimeError(f"Failed to create answer: {response.error}")

        self.logger.debug(f"Answer created for session {session_id}")
        return response.sdp

    async def set_remote_description(self, session_id: str, remote_sdp: str, sdp_type: str):
        """
        Set remote SDP description (for outgoing calls after receiving session-accept).

        Args:
            session_id: Session ID
            remote_sdp: Remote SDP string
            sdp_type: "offer" or "answer"
        """
        if not self._stub:
            raise RuntimeError("gRPC stub not initialized")

        self.logger.debug(f"Setting remote description for session {session_id} (type={sdp_type})")

        request = call_pb2.SetRemoteDescriptionRequest(
            session_id=session_id,
            remote_sdp=remote_sdp,
            sdp_type=sdp_type
        )

        await self._stub.SetRemoteDescription(request)
        self.logger.debug(f"Remote description set for session {session_id}")

    async def add_ice_candidate(self, session_id: str, candidate: Dict[str, Any]):
        """
        Add ICE candidate to session.

        Args:
            session_id: Session ID
            candidate: ICE candidate dict (must have 'candidate', 'sdpMid', 'sdpMLineIndex')
        """
        if not self._stub:
            raise RuntimeError("gRPC stub not initialized")

        self.logger.debug(f"Adding ICE candidate to session {session_id}")

        request = call_pb2.AddICECandidateRequest(
            session_id=session_id,
            candidate=candidate.get('candidate', ''),
            sdp_mid=candidate.get('sdpMid', ''),
            sdp_mline_index=candidate.get('sdpMLineIndex', 0)
        )

        await self._stub.AddICECandidate(request)
        self.logger.debug(f"ICE candidate added to session {session_id}")

    async def get_stats(self, session_id: str) -> Dict[str, Any]:
        """
        Get call statistics for a session.

        Args:
            session_id: Session ID

        Returns:
            Dict with stats: connection_state, ice_connection_state, packets_sent, etc.
        """
        if not self._stub:
            raise RuntimeError("gRPC stub not initialized")

        request = call_pb2.GetStatsRequest(session_id=session_id)
        response = await self._stub.GetStats(request)

        # Convert proto response to dict
        return {
            'connection_state': response.connection_state,
            'ice_connection_state': response.ice_connection_state,
            'ice_gathering_state': response.ice_gathering_state,
            'bytes_sent': response.bytes_sent,
            'bytes_received': response.bytes_received,
            'bandwidth_kbps': response.bandwidth_kbps,
            'local_candidates': list(response.local_candidates),
            'remote_candidates': list(response.remote_candidates),
            'connection_type': response.connection_type,
        }

    async def list_audio_devices(self) -> list:
        """
        List available audio devices.

        Returns:
            List of dicts with keys: name (device ID), description (friendly name), device_class (Audio/Source or Audio/Sink)
        """
        if not self._stub:
            self.logger.error("gRPC stub not initialized")
            return []

        try:
            request = call_pb2.Empty()
            response = await self._stub.ListAudioDevices(request)

            devices = []
            for device in response.devices:
                devices.append({
                    'name': device.name,
                    'description': device.description,
                    'device_class': device.device_class
                })

            self.logger.info(f"Listed {len(devices)} audio devices")
            return devices
        except Exception as e:
            self.logger.error(f"Failed to list audio devices: {e}")
            return []

    async def set_mute(self, session_id: str, muted: bool):
        """
        Set microphone mute state for a session.

        Args:
            session_id: Session ID
            muted: True to mute microphone, False to unmute
        """
        if not self._stub:
            raise RuntimeError("gRPC stub not initialized")

        self.logger.info(f"Setting mute state for session {session_id}: muted={muted}")

        request = call_pb2.SetMuteRequest(
            session_id=session_id,
            muted=muted
        )

        await self._stub.SetMute(request)
        self.logger.info(f"Mute state set for session {session_id}")

    async def end_session(self, session_id: str):
        """
        End call session.

        Args:
            session_id: Session ID
        """
        # Stop event stream first
        await self._stop_event_stream(session_id)

        if not self._stub:
            self.logger.warning("gRPC stub not initialized, cannot end session")
            return

        self.logger.info(f"Ending session {session_id}")

        request = call_pb2.EndSessionRequest(session_id=session_id)
        await self._stub.EndSession(request)

        self.logger.info(f"Session {session_id} ended")

    async def _start_event_stream(self, session_id: str):
        """
        Start event streaming task for a session.

        Args:
            session_id: Session ID to stream events for
        """
        async with self._stream_lock:
            if session_id in self._event_streams:
                self.logger.warning(f"Event stream already running for {session_id}")
                return

            # Create and start streaming task
            task = asyncio.create_task(self._consume_events(session_id))
            self._event_streams[session_id] = task
            self.logger.info(f"Started event stream for {session_id}")

    async def _stop_event_stream(self, session_id: str):
        """
        Stop event streaming task for a session.

        Args:
            session_id: Session ID to stop streaming for
        """
        async with self._stream_lock:
            task = self._event_streams.pop(session_id, None)
            if task and not task.done():
                self.logger.debug(f"Cancelling event stream for {session_id}")
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _consume_events(self, session_id: str):
        """
        Consume events from Go service for a session (with reconnection).

        This runs in a background task and calls registered callbacks
        when events arrive from the Go service.

        Args:
            session_id: Session ID to consume events for
        """
        max_retries = 3
        retry_delay = 1.0  # Start with 1 second

        for attempt in range(max_retries + 1):
            try:
                if attempt > 0:
                    self.logger.info(
                        f"Reconnecting event stream for {session_id} "
                        f"(attempt {attempt}/{max_retries})"
                    )
                    await asyncio.sleep(retry_delay)
                    retry_delay = min(retry_delay * 2, 10.0)  # Exponential backoff, max 10s

                # Start streaming
                self.logger.debug(f"Opening event stream for {session_id}")
                request = call_pb2.StreamEventsRequest(session_id=session_id)

                if not self._stub:
                    self.logger.error("gRPC stub not initialized")
                    return

                async for event in self._stub.StreamEvents(request):
                    await self._handle_event(session_id, event)

                # Stream ended normally (channel closed)
                self.logger.info(f"Event stream ended normally for {session_id}")
                return

            except grpc.aio.AioRpcError as e:
                if e.code() == grpc.StatusCode.CANCELLED:
                    self.logger.debug(f"Event stream cancelled for {session_id}")
                    return
                elif e.code() == grpc.StatusCode.UNAVAILABLE:
                    self.logger.warning(
                        f"Go service unavailable for {session_id}: {e.details()}"
                    )
                    if attempt >= max_retries:
                        self.logger.error(
                            f"Max retries reached for {session_id}, giving up"
                        )
                        return
                    # Otherwise retry
                else:
                    self.logger.error(
                        f"gRPC error in event stream for {session_id}: "
                        f"{e.code()} - {e.details()}"
                    )
                    return

            except asyncio.CancelledError:
                self.logger.debug(f"Event stream task cancelled for {session_id}")
                raise  # Re-raise to properly handle cancellation

            except Exception as e:
                self.logger.error(
                    f"Unexpected error in event stream for {session_id}: {e}"
                )
                import traceback
                self.logger.error(traceback.format_exc())
                return

    async def _handle_event(self, session_id: str, event: call_pb2.CallEvent):
        """
        Handle a single event from Go service.

        Args:
            session_id: Session ID
            event: CallEvent from protobuf
        """
        event_type = event.WhichOneof('event')

        if event_type == 'ice_candidate':
            # ICE candidate event
            ice_event = event.ice_candidate
            candidate = {
                'candidate': ice_event.candidate,
                'sdpMid': ice_event.sdp_mid,
                'sdpMLineIndex': ice_event.sdp_mline_index,
            }
            self.logger.debug(
                f"ICE candidate event for {session_id}: {ice_event.candidate[:50]}..."
            )

            if self.on_ice_candidate:
                try:
                    # Call callback (may be sync or async)
                    result = self.on_ice_candidate(session_id, candidate)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    self.logger.error(f"Error in on_ice_candidate callback: {e}")
                    import traceback
                    self.logger.error(traceback.format_exc())

        elif event_type == 'connection_state':
            # Connection state event
            state_event = event.connection_state
            state_map = {
                call_pb2.ConnectionStateEvent.NEW: 'new',
                call_pb2.ConnectionStateEvent.CHECKING: 'checking',
                call_pb2.ConnectionStateEvent.CONNECTED: 'connected',
                call_pb2.ConnectionStateEvent.COMPLETED: 'completed',
                call_pb2.ConnectionStateEvent.FAILED: 'failed',
                call_pb2.ConnectionStateEvent.DISCONNECTED: 'disconnected',
                call_pb2.ConnectionStateEvent.CLOSED: 'closed',
            }
            state_str = state_map.get(state_event.state, 'unknown')

            self.logger.info(f"Connection state for {session_id}: {state_str}")

            if self.on_connection_state:
                try:
                    # Call callback (may be sync or async)
                    result = self.on_connection_state(session_id, state_str)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception as e:
                    self.logger.error(f"Error in on_connection_state callback: {e}")
                    import traceback
                    self.logger.error(traceback.format_exc())

        elif event_type == 'error':
            # Error event
            error_event = event.error
            self.logger.error(
                f"Error event from Go service for {session_id}: {error_event.message}"
            )

        else:
            self.logger.warning(f"Unknown event type for {session_id}: {event_type}")
