import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import requests
import os
import threading
import datetime
import sys
import subprocess

class LoadingSplash:
    """Splash screen with loading bar shown during startup checks"""
    def __init__(self, root):
        self.root = root
        self.splash = tk.Toplevel(root)
        self.splash.title("LM Studio Orchestrator")
        self.splash.geometry("400x150")
        self.splash.resizable(False, False)
        
        # Center on screen
        self.splash.update_idletasks()
        x = (self.splash.winfo_screenwidth() // 2) - 200
        y = (self.splash.winfo_screenheight() // 2) - 75
        self.splash.geometry(f"+{x}+{y}")
        
        # Remove window decorations for cleaner look
        self.splash.attributes('-topmost', True)
        
        # Content
        ttk.Label(self.splash, text="LM Studio Multi-Model Orchestrator", font=("Arial", 12, "bold")).pack(pady=15)
        ttk.Label(self.splash, text="Checking server...", font=("Arial", 9)).pack(pady=(0, 10))
        
        self.progress = ttk.Progressbar(self.splash, mode='indeterminate', length=300)
        self.progress.pack(pady=10)
        self.progress.start()
    
    def update_status(self, text):
        """Update status text on splash screen"""
        self.status_text.config(text=text)
        self.splash.update()
    
    def close(self):
        """Close splash screen"""
        self.progress.stop()
        self.splash.destroy()



class LMStudioOrchestrator:
    def __init__(self, root):
        self.root = root
        self.root.title("LM Studio Multi-Model Orchestrator")
        self.root.geometry("900x624")
        self.root.resizable(False, False)  # Start locked
        
        # Window lock state
        self.window_locked = True
        
        # API Config
        self.api_base = "http://localhost:1234"
        self.model_keys = {}
        self.current_model = None
        self.loading_model = False
        
        # Document settings
        self.max_tokens_var = tk.IntVar(value=2000)
        self.temperature_var = tk.DoubleVar(value=0.7)
        
        # Token tracking
        self.session_tokens = 0
        self.reasoning_tokens = 0
        
        # Logging
        self.error_log = []
        # Set up log file
        self.log_file_path = os.path.join(os.path.expanduser("~"), "Downloads", "lm_studio_logs.txt")
        self._initialize_log_file()
        
        # ===== TOP TAB MENU BAR =====
        menu_bar = tk.Frame(self.root, bg="#f0f0f0", relief=tk.RIDGE, bd=1)
        menu_bar.pack(fill=tk.X, side=tk.TOP)
        menu_bar.grid_rowconfigure(0, weight=1)
        
        # File Menu (Exit button)
        file_menu_btn = tk.Button(
            menu_bar,
            text="File",
            font=("Arial", 10, "bold"),
            bg="#e8e8e8",
            activebackground="#d0d0d0",
            relief=tk.FLAT,
            bd=0,
            pady=8,
            command=self.show_file_menu,
            justify=tk.CENTER
        )
        file_menu_btn.grid(row=0, column=0, sticky="nsew", padx=3, pady=5)
        menu_bar.grid_columnconfigure(0, weight=0, minsize=50)
        
        self.tab_buttons = {}
        self.tab_frames = {}
        self.current_tab = "model_control"
        
        # Tab button specs
        tabs = [
            ("model_control", "Model Control hidden, tap to open")
        ]
        
        for idx, (tab_id, tab_label) in enumerate(tabs, start=1):
            btn = tk.Button(
                menu_bar,
                text=tab_label,
                font=("Arial", 10, "bold"),
                bg="#e8e8e8",
                activebackground="#d0d0d0",
                relief=tk.FLAT,
                bd=0,
                pady=8,
                command=lambda tid=tab_id: self.switch_tab(tid),
                justify=tk.CENTER
            )
            btn.grid(row=0, column=idx, sticky="nsew", padx=3, pady=5)
            menu_bar.grid_columnconfigure(idx, weight=1)
            self.tab_buttons[tab_id] = btn
        
        # Update tab appearance to highlight active tab
        self.update_tab_appearance()
        
        # ===== MAIN CONTAINER FRAME =====
        self.main_container = tk.Frame(self.root, bg="#f0f2f7")
        self.main_container.pack(fill=tk.BOTH, expand=True)
        
        # ===== TAB 1: MODEL CONTROL =====
        self.model_control_frame = tk.Frame(self.main_container, bg="#f0f2f7")
        self.tab_frames["model_control"] = self.model_control_frame
        
        # Scrollable container for model control
        canvas = tk.Canvas(self.model_control_frame, bg="#f0f2f7", highlightthickness=0)
        scrollbar = ttk.Scrollbar(self.model_control_frame, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas, padding=20)
        scrollable_frame.configure(style='TFrame')
        
        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set, bg="#f0f2f7")
        
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bind mousewheel
        def _on_mousewheel(event):
            try:
                if canvas.winfo_exists():
                    canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            except:
                pass
        canvas.bind("<MouseWheel>", _on_mousewheel)
        scrollable_frame.bind("<MouseWheel>", _on_mousewheel)
        
        # ===== CONTROL PANEL (Card 1) =====
        control_panel = tk.Frame(scrollable_frame, bg="white", relief=tk.FLAT, bd=0, highlightthickness=3, highlightbackground="#e8ecf4", highlightcolor="#e8ecf4")
        control_panel.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 20))
        
        # Add card header
        card_header = tk.Frame(control_panel, bg="white")
        card_header.pack(fill=tk.X, padx=18, pady=(18, 12))
        ttk.Label(card_header, text="Model Control", font=("Arial", 11, "bold"), background="white").pack(anchor=tk.W)
        
        # Card content container
        card_content = tk.Frame(control_panel, bg="white")
        card_content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        
        # Connection status
        ttk.Label(card_content, text="LM Studio Status:", font=("Arial", 10, "bold"), background="white").pack(anchor=tk.W, pady=(0, 5))
        
        status_row = tk.Frame(card_content, bg="white")
        status_row.pack(fill=tk.X, pady=(0, 5))
        
        self.status_label = tk.Label(status_row, text="❌ Disconnected", foreground="red", font=("Arial", 9), bg="white")
        self.status_label.pack(side=tk.LEFT, padx=(0, 15))
        
        tk.Label(status_row, text="Server:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 5))
        self.server_label = tk.Label(status_row, text=self.api_base, foreground="blue", font=("Courier", 9), bg="white")
        self.server_label.pack(side=tk.LEFT)
        
        # Window resolution display with lock button
        resolution_frame = tk.Frame(status_row, bg="white")
        resolution_frame.pack(side=tk.RIGHT)
        
        self.resolution_label = tk.Label(resolution_frame, text="491x624", foreground="blue", font=("Courier", 9), bg="white")
        self.resolution_label.pack(side=tk.LEFT, padx=(0, 5))
        
        self.lock_button = tk.Button(resolution_frame, text="🔒", font=("Arial", 8), command=self.toggle_window_lock, relief=tk.FLAT, bd=0, padx=3, pady=0, bg="white")
        self.lock_button.pack(side=tk.LEFT)
        
        # Model load status
        model_status_row = tk.Frame(card_content, bg="white")
        model_status_row.pack(anchor=tk.W, pady=(0, 10))
        
        self.model_status_label = tk.Label(model_status_row, text="❌ No Model Loaded", foreground="red", font=("Arial", 9), bg="white")
        self.model_status_label.pack(side=tk.LEFT, padx=(0, 15))
        
        tk.Label(model_status_row, text="Model:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 5))
        self.model_name_label = tk.Label(model_status_row, text="Waiting...", foreground="blue", font=("Courier", 9), bg="white")
        self.model_name_label.pack(side=tk.LEFT)
        
        # Token tracking
        token_row = tk.Frame(card_content, bg="white")
        token_row.pack(anchor=tk.W, pady=(0, 10))
        
        tk.Label(token_row, text="Tokens used:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 10))
        self.session_tokens_label = tk.Label(token_row, text="0", foreground="green", font=("Courier", 9, "bold"), bg="white")
        self.session_tokens_label.pack(side=tk.LEFT, padx=(0, 20))
        
        tk.Label(token_row, text="Reasoning:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 10))
        self.reasoning_tokens_label = tk.Label(token_row, text="0", foreground="blue", font=("Courier", 9, "bold"), bg="white")
        self.reasoning_tokens_label.pack(side=tk.LEFT)
        
        # Global max tokens and temperature settings
        global_settings_row = tk.Frame(card_content, bg="white")
        global_settings_row.pack(fill=tk.X, pady=(0, 12))
        
        tk.Label(global_settings_row, text="Max Tokens:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Spinbox(global_settings_row, from_=100, to=32000, textvariable=self.max_tokens_var, width=10).pack(side=tk.LEFT, padx=(0, 20))
        
        tk.Label(global_settings_row, text="Temperature:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 5))
        ttk.Spinbox(global_settings_row, from_=0.0, to=2.0, increment=0.1, textvariable=self.temperature_var, width=10).pack(side=tk.LEFT)
        
        tk.Button(card_content, text="Refresh Script", command=self.refresh_script, bg="red", fg="white", font=("Arial", 9)).pack(fill=tk.X, pady=3)
        tk.Button(card_content, text="Restart Script", command=self.restart_script, bg="red", fg="white", font=("Arial", 9)).pack(fill=tk.X, pady=3)
        
        # ===== MODEL MANAGEMENT (Card 2) =====
        model_card = tk.Frame(scrollable_frame, bg="white", relief=tk.FLAT, bd=0, highlightthickness=3, highlightbackground="#e8ecf4", highlightcolor="#e8ecf4")
        model_card.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 20))
        
        # Card header
        model_card_header = tk.Frame(model_card, bg="white")
        model_card_header.pack(fill=tk.X, padx=18, pady=(18, 12))
        ttk.Label(model_card_header, text="Model Management", font=("Arial", 11, "bold"), background="white").pack(anchor=tk.W)
        
        # Card content
        model_card_content = tk.Frame(model_card, bg="white")
        model_card_content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        
        tk.Label(model_card_content, text="Available Models:", font=("Arial", 9), bg="white").pack(anchor=tk.W, pady=(0, 3))
        tk.Label(model_card_content, text="(double-click to load)", font=("Arial", 8), foreground="gray", bg="white").pack(anchor=tk.W, pady=(0, 5))
        
        self.models_listbox = tk.Listbox(model_card_content, height=4, font=("Arial", 9), selectmode=tk.SINGLE)
        self.models_listbox.pack(fill=tk.X, pady=(0, 8))
        self.models_listbox.bind('<Double-Button-1>', self.on_model_select)
        self.models_listbox.bind('<Motion>', self.on_listbox_hover)
        
        # ===== STORY CASCADE (Card 3) =====
        cascade_card = tk.Frame(scrollable_frame, bg="white", relief=tk.FLAT, bd=0, highlightthickness=3, highlightbackground="#e8ecf4", highlightcolor="#e8ecf4")
        cascade_card.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 20))
        
        # Card header
        cascade_card_header = tk.Frame(cascade_card, bg="white")
        cascade_card_header.pack(fill=tk.X, padx=18, pady=(18, 12))
        ttk.Label(cascade_card_header, text="Story Cascade", font=("Arial", 11, "bold"), background="white").pack(anchor=tk.W)
        
        # Card content with proper sizing
        cascade_card_content = tk.Frame(cascade_card, bg="white")
        cascade_card_content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        
        # Story prompt input (MOVED TO TOP)
        tk.Label(cascade_card_content, text="Story Idea/Prompt:", font=("Arial", 9), bg="white").pack(anchor=tk.W, pady=(0, 3))
        
        self.cascade_input = tk.Text(cascade_card_content, height=3, font=("Arial", 9), wrap=tk.WORD, 
                                     bg="white", fg="#000", relief=tk.SOLID, bd=1)
        self.cascade_input.pack(fill=tk.X, pady=(0, 15))
        self.cascade_input.insert("1.0", "A mysterious figure arrives in a small town...")
        
        # Model selection for 5-pass cascade
        tk.Label(cascade_card_content, text="Select Model for Each Pass:", font=("Arial", 9), bg="white").pack(anchor=tk.W, pady=(0, 5))
        tk.Label(cascade_card_content, text="(leave blank to skip pass)", font=("Arial", 8), foreground="gray", bg="white").pack(anchor=tk.W, pady=(0, 8))
        
        # Store cascade pass selections
        self.cascade_pass_vars = {}
        self.cascade_pass_dropdowns = {}
        self.cascade_pass_max_tokens = {}  # Per-pass token limits
        self.model_display_names = {}
        
        # Create 5 pass dropdowns with per-pass token limits
        for pass_num in range(1, 6):
            pass_frame = tk.Frame(cascade_card_content, bg="white")
            pass_frame.pack(fill=tk.X, pady=(0, 10))
            
            pass_label_text = f"Pass {pass_num}"
            if pass_num == 1:
                pass_label_text += " (Create)"
            elif pass_num == 5:
                pass_label_text += " (Polish)"
            else:
                pass_label_text += " (Refine)"
            
            tk.Label(pass_frame, text=pass_label_text, font=("Arial", 9), width=14, bg="white").pack(side=tk.LEFT, padx=(0, 8))
            
            var = tk.StringVar(value="None Selected")
            self.cascade_pass_vars[pass_num] = var
            
            dropdown = ttk.Combobox(pass_frame, textvariable=var, state="readonly", width=22)
            dropdown.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
            self.cascade_pass_dropdowns[pass_num] = dropdown
            
            # Per-pass max tokens
            tk.Label(pass_frame, text="Tokens:", font=("Arial", 8), bg="white").pack(side=tk.LEFT, padx=(0, 5))
            tokens_var = tk.IntVar(value=2000)
            self.cascade_pass_max_tokens[pass_num] = tokens_var
            tokens_spinbox = ttk.Spinbox(pass_frame, from_=100, to=4000, textvariable=tokens_var, width=5)
            tokens_spinbox.pack(side=tk.LEFT, padx=(0, 0))
        
        # Temperature limiter (global for all passes)
        limiter_frame = tk.Frame(cascade_card_content, bg="white", relief=tk.FLAT, bd=0, highlightthickness=0)
        limiter_frame.pack(fill=tk.X, pady=(15, 10))
        
        # Single row with Temperature on left and Buttons on right
        settings_row = tk.Frame(limiter_frame, bg="white")
        settings_row.pack(fill=tk.X, pady=(8, 8))
        
        # Left side: Temperature
        left_frame = tk.Frame(settings_row, bg="white")
        left_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        tk.Label(left_frame, text="Temperature (Global):", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 5))
        self.cascade_temp_var = tk.DoubleVar(value=0.8)
        temp_spinbox = ttk.Spinbox(left_frame, from_=0.0, to=2.0, increment=0.1, textvariable=self.cascade_temp_var, width=8)
        temp_spinbox.pack(side=tk.LEFT, padx=(0, 15))
        tk.Label(left_frame, text="(0.0-2.0)", font=("Arial", 8), foreground="gray", bg="white").pack(side=tk.LEFT)
        
        # Right side: Buttons
        right_frame = tk.Frame(settings_row, bg="white")
        right_frame.pack(side=tk.RIGHT, fill=tk.X)
        
        tk.Button(right_frame, text="▶ Generate", command=self.start_cascade, bg="green", fg="white", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 5))
        tk.Button(right_frame, text="🗑️ Clear All", command=self.clear_cascade, bg="red", fg="white", font=("Arial", 9)).pack(side=tk.LEFT)
        
        # Output section
        tk.Label(cascade_card_content, text="Current Story Version:", font=("Arial", 9), bg="white").pack(anchor=tk.W, pady=(0, 5))
        
        cascade_output_frame = tk.Frame(cascade_card_content, bg="white", relief=tk.SOLID, bd=1)
        cascade_output_frame.pack(fill=tk.BOTH, expand=False, pady=(0, 10))
        
        self.cascade_output = tk.Text(cascade_output_frame, height=6, font=("Courier", 9), wrap=tk.WORD,
                                      bg="#f9f9f9", fg="#000", relief=tk.SOLID, bd=0, state=tk.DISABLED)
        self.cascade_output.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        
        cascade_btn_frame = tk.Frame(cascade_card_content, bg="white")
        cascade_btn_frame.pack(fill=tk.X, pady=(0, 0))
        tk.Button(cascade_btn_frame, text="📋 Copy Output", command=self.copy_cascade_output, bg="blue", fg="white", font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 3))
        tk.Button(cascade_btn_frame, text="💾 Save Story", command=self.save_cascade_story, bg="blue", fg="white", font=("Arial", 9)).pack(side=tk.LEFT)
        
        # ===== CHAT (Card 4) =====
        chat_card = tk.Frame(scrollable_frame, bg="white", relief=tk.FLAT, bd=0, highlightthickness=3, highlightbackground="#e8ecf4", highlightcolor="#e8ecf4")
        chat_card.pack(fill=tk.BOTH, expand=False, padx=12, pady=(0, 20))
        
        # Card header
        chat_card_header = tk.Frame(chat_card, bg="white")
        chat_card_header.pack(fill=tk.X, padx=18, pady=(18, 12))
        
        # Title on left
        ttk.Label(chat_card_header, text="Chat", font=("Arial", 11, "bold"), background="white").pack(side=tk.LEFT, anchor=tk.W)
        
        # Model dropdown on right
        dropdown_frame = tk.Frame(chat_card_header, bg="white")
        dropdown_frame.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(15, 0))
        
        tk.Label(dropdown_frame, text="Model:", font=("Arial", 9), bg="white").pack(side=tk.LEFT, padx=(0, 5))
        
        self.chat_model_var = tk.StringVar(value="None Selected")
        chat_model_dropdown = ttk.Combobox(dropdown_frame, textvariable=self.chat_model_var, state="readonly", width=25)
        chat_model_dropdown.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.chat_model_dropdown = chat_model_dropdown
        
        # Chat content
        chat_card_content = tk.Frame(chat_card, bg="white")
        chat_card_content.pack(fill=tk.BOTH, expand=True, padx=18, pady=(0, 18))
        
        # Messages container with scrollbar
        messages_container = tk.Frame(chat_card_content, bg="white", height=200)
        messages_container.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        messages_container.pack_propagate(False)
        
        self.chat_canvas = tk.Canvas(messages_container, bg="white", highlightthickness=0)
        chat_scrollbar = ttk.Scrollbar(messages_container, orient="vertical", command=self.chat_canvas.yview)
        self.chat_messages_frame = tk.Frame(self.chat_canvas, bg="white")
        
        self.chat_messages_frame.bind(
            "<Configure>",
            lambda e: self.chat_canvas.configure(scrollregion=self.chat_canvas.bbox("all"))
        )
        
        self.chat_canvas.create_window((0, 0), window=self.chat_messages_frame, anchor="nw")
        self.chat_canvas.configure(yscrollcommand=chat_scrollbar.set)
        
        self.chat_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        chat_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Bind mousewheel
        def _on_chat_mousewheel(event):
            try:
                if self.chat_canvas.winfo_exists():
                    self.chat_canvas.yview_scroll(int(-1*(event.delta/120)), "units")
            except:
                pass
        
        self.chat_canvas.bind("<MouseWheel>", _on_chat_mousewheel)
        self.chat_messages_frame.bind("<MouseWheel>", _on_chat_mousewheel)
        
        # Input frame
        input_frame = tk.Frame(chat_card_content, bg="white")
        input_frame.pack(fill=tk.X, pady=(0, 0))
        
        # Input field
        input_sub_frame = tk.Frame(input_frame, bg="white")
        input_sub_frame.pack(fill=tk.X, expand=True, pady=(0, 8))
        
        self.chat_input = tk.Text(input_sub_frame, height=2, font=("Arial", 9), wrap=tk.WORD, 
                                   bg="white", fg="#000", relief=tk.SOLID, bd=1)
        self.chat_input.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        self.chat_input.bind('<Control-Return>', lambda e: self.send_chat_message())
        
        # Send button
        send_btn = tk.Button(input_sub_frame, text="➤", font=("Arial", 12, "bold"), 
                            bg="#0084ff", fg="white", relief=tk.FLAT, bd=0, padx=12, pady=4,
                            command=self.send_chat_message, cursor="hand2")
        send_btn.pack(side=tk.LEFT)
        
        # Chat options
        chat_options_frame = tk.Frame(chat_card_content, bg="white")
        chat_options_frame.pack(fill=tk.X)
        
        tk.Button(chat_options_frame, text="🗑️ Clear", command=self.clear_chat_messages, bg="red", fg="white", font=("Arial", 9)).pack(side=tk.LEFT)
        
        # Store message labels for chat
        self.chat_message_labels = []
        # Store cascade running state
        self.cascade_running = False
        
        # Initialize logs_listbox as None (no UI display, only file logging)
        self.logs_listbox = None
        
        # Store model display names for dropdown updates
        self.model_display_names = {}
        
        # Bind window resize event
        self.root.bind("<Configure>", self.update_resolution)
        
        # Bind window close (X button) to clean exit
        self.root.protocol("WM_DELETE_WINDOW", self.exit_app)
        
        # Run startup checks
        self.run_startup_checks()
    
    def switch_tab(self, tab_id):
        """Switch to a different tab"""
        # Hide all frames
        for frame in self.tab_frames.values():
            frame.pack_forget()
        
        # Show selected frame
        if tab_id in self.tab_frames:
            self.tab_frames[tab_id].pack(fill=tk.BOTH, expand=True)
            self.current_tab = tab_id
            self.update_tab_appearance()
    
    def update_tab_appearance(self):
        """Update tab button styling to highlight active tab"""
        for tab_id, btn in self.tab_buttons.items():
            if tab_id == self.current_tab:
                btn.config(bg="#4a9eff", fg="white")
            else:
                btn.config(bg="#e8e8e8", fg="black")
    
    def _initialize_log_file(self):
        """Create or append to log file with session separator"""
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(f"\n{'='*60}\nSession started: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n{'='*60}\n")
        except Exception as e:
            pass  # Silently fail
    
    def show_file_menu(self):
        """Show File menu with View Log and Exit options"""
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="View Log File", command=self.open_log_file)
        menu.add_separator()
        menu.add_command(label="Exit", command=self.exit_app)
        
        # Get File button position for menu placement
        try:
            menu.post(self.root.winfo_x(), self.root.winfo_y() + 50)
        except:
            pass  # Fallback if positioning fails
    
    def exit_app(self):
        """Clean exit - save logs and close app"""
        self.add_log("Application closing", "SYSTEM")
        self.root.quit()
    
    def open_log_file(self):
        """Open the log file with default application"""
        if not os.path.exists(self.log_file_path):
            messagebox.showwarning("No Log File", f"Log file not found:\n{self.log_file_path}")
            return
        
        try:
            if os.name == 'nt':  # Windows
                os.startfile(self.log_file_path)
            else:  # macOS and Linux
                os.system(f'open "{self.log_file_path}"')
            self.add_log("Opened log file", "INFO")
        except Exception as e:
            messagebox.showerror("Error", f"Could not open log file:\n{str(e)}")
            self.add_log(f"Error opening log file: {str(e)}", "ERROR")
    
    def add_log(self, message, log_type="INFO"):
        """Add a message to the error logs and save to file"""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        log_entry = f"[{timestamp}] {log_type}: {message}"
        self.error_log.append(log_entry)
        
        # Write to log file
        try:
            with open(self.log_file_path, 'a', encoding='utf-8') as f:
                f.write(log_entry + "\n")
        except Exception as e:
            pass  # Silently fail if log write fails
        
        # Update listbox if it exists
        if self.logs_listbox and self.logs_listbox.winfo_exists():
            self.logs_listbox.insert(tk.END, log_entry)
            self.logs_listbox.see(tk.END)
        
        # Keep last 1000 entries in memory
        if len(self.error_log) > 1000:
            self.error_log.pop(0)
            if self.logs_listbox and self.logs_listbox.winfo_exists():
                self.logs_listbox.delete(0, 1)
    

    
    def clear_logs(self):
        """Clear all logs from memory and file"""
        if messagebox.askyesno("Clear Logs", "Clear all logs?"):
            if self.logs_listbox and self.logs_listbox.winfo_exists():
                self.logs_listbox.delete(0, tk.END)
            self.error_log.clear()
            # Clear log file
            try:
                with open(self.log_file_path, 'w', encoding='utf-8') as f:
                    f.write("")
            except:
                pass
            self.add_log("Logs cleared", "SYSTEM")
    
    def copy_log_entry(self):
        """Copy selected log entry to clipboard"""
        selection = self.logs_listbox.curselection()
        if selection:
            text = self.logs_listbox.get(selection[0])
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
            messagebox.showinfo("Copied", "Log entry copied!")
        else:
            messagebox.showwarning("No Selection", "Select a log entry first.")

    
    def run_startup_checks(self):
        """Run startup checks in background with loading splash"""
        splash = LoadingSplash(self.root)
        self.add_log("Starting application...", "SYSTEM")
        
        def startup_thread():
            self.is_connected = self.check_connection()
            self.refresh_models()
            splash.close()
            self.root.deiconify()
            
            # Show troubleshooting dialog if connection or models are missing
            self.root.after(500, self.show_troubleshooting_dialog)
        
        thread = threading.Thread(target=startup_thread, daemon=True)
        thread.start()
    
    def show_troubleshooting_dialog(self):
        """Show troubleshooting dialog if connection or models are missing"""
        needs_help = False
        message = ""
        
        # Check if disconnected
        if not self.is_connected:
            needs_help = True
            message += "❌ LM STUDIO NOT CONNECTED\n"
            message += "━" * 45 + "\n"
            message += "• Make sure LM Studio server is running\n"
            message += "• Check the server is on http://localhost:1234\n"
            message += "• Press the red 'Restart Script' button below\n\n"
        
        # Check if no models loaded
        model_count = self.models_listbox.size()
        if model_count == 0:
            needs_help = True
            message += "❌ NO MODELS AVAILABLE\n"
            message += "━" * 45 + "\n"
            message += "• Make sure LM Studio server is running\n"
            message += "• Load a model in LM Studio before starting\n"
            message += "• Press the red 'Restart Script' button below\n\n"
        
        if needs_help:
            # Create troubleshooting window
            troubleshoot_win = tk.Toplevel(self.root)
            troubleshoot_win.title("⚠️  Troubleshooting")
            troubleshoot_win.geometry("500x300")
            troubleshoot_win.resizable(False, False)
            troubleshoot_win.grab_set()
            
            # Center on parent window
            self.root.update_idletasks()
            x = self.root.winfo_x() + (self.root.winfo_width() // 2) - 250
            y = self.root.winfo_y() + (self.root.winfo_height() // 2) - 150
            troubleshoot_win.geometry(f"+{x}+{y}")
            
            # Main frame with padding
            main_frame = tk.Frame(troubleshoot_win, bg="white")
            main_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=20)
            
            # Title
            title_label = tk.Label(main_frame, text="Connection Issues Detected", 
                                 font=("Arial", 13, "bold"), bg="white", fg="#dc2626")
            title_label.pack(anchor=tk.W, pady=(0, 15))
            
            # Message
            msg_label = tk.Label(main_frame, text=message, font=("Arial", 10), 
                               bg="white", fg="#333", justify=tk.LEFT, wraplength=450)
            msg_label.pack(anchor=tk.W, pady=(0, 20), fill=tk.BOTH, expand=True)
            
            # Separator
            separator = tk.Frame(main_frame, bg="#e8ecf4", height=1)
            separator.pack(fill=tk.X, pady=(0, 15))
            
            # Button frame
            btn_frame = tk.Frame(main_frame, bg="white")
            btn_frame.pack(fill=tk.X)
            
            tk.Button(btn_frame, text="Press Restart Script →", 
                     command=lambda: [self.restart_script(), troubleshoot_win.destroy()],
                     bg="#0084ff", fg="white", font=("Arial", 10, "bold"),
                     padx=15, pady=8, relief=tk.FLAT, cursor="hand2").pack(side=tk.LEFT, padx=(0, 10))
            
            tk.Button(btn_frame, text="Got it", command=troubleshoot_win.destroy,
                     bg="#f3f4f6", fg="#333", font=("Arial", 10),
                     padx=15, pady=8, relief=tk.FLAT, cursor="hand2").pack(side=tk.LEFT)
    
    def update_resolution(self, event=None):
        """Update window resolution display"""
        width = self.root.winfo_width()
        height = self.root.winfo_height()
        if width > 1 and height > 1:
            self.resolution_label.config(text=f"{width}x{height}")
    
    def toggle_window_lock(self):
        """Toggle window resizing lock"""
        self.window_locked = not self.window_locked
        self.root.resizable(not self.window_locked, not self.window_locked)
        self.lock_button.config(text="🔒" if self.window_locked else "🔓")
    
    def refresh_models(self):
        """Fetch available models from LM Studio"""
        try:
            response = requests.get(f"{self.api_base}/api/v1/models", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                
                self.models_listbox.delete(0, tk.END)
                self.model_keys = {}
                
                llm_models = [m for m in models if m.get("type") == "llm"]
                
                if llm_models:
                    for idx, model in enumerate(llm_models):
                        display_name = model.get("display_name", "Unknown")
                        key = model.get("key", "unknown")
                        quantization = model.get("quantization", {}).get("name", "")
                        
                        display_text = f"{display_name} ({quantization})"
                        
                        self.models_listbox.insert(tk.END, display_text)
                        self.model_keys[idx] = key
                    self.add_log(f"Loaded {len(llm_models)} models", "INFO")
                else:
                    self.models_listbox.insert(tk.END, "No LLM models found")
                    self.add_log("No LLM models found", "WARNING")
                
                self.models_listbox.selection_clear(0, tk.END)
                self.update_cascade_models()  # Update cascade model keys
            else:
                self.models_listbox.delete(0, tk.END)
                self.models_listbox.insert(tk.END, "Failed to load models")
                self.add_log(f"Failed to load models: {response.status_code}", "ERROR")
        except Exception as e:
            self.models_listbox.delete(0, tk.END)
            self.models_listbox.insert(tk.END, f"Error: {str(e)}")
            self.add_log(f"Error fetching models: {str(e)}", "ERROR")
    
    def on_model_select(self, event):
        """Handle model selection from listbox"""
        if self.loading_model:
            return
        
        idx = self.models_listbox.nearest(event.y)
        if idx >= 0:
            model_key = self.model_keys.get(idx)
            if model_key:
                self.load_model(model_key)
    
    def on_listbox_hover(self, event):
        """Highlight listbox item on hover"""
        idx = self.models_listbox.nearest(event.y)
        if idx >= 0:
            self.models_listbox.selection_clear(0, tk.END)
            self.models_listbox.selection_set(idx)
            self.models_listbox.see(idx)
    
    def load_model(self, model_key):
        """Load a model by its key"""
        if not model_key:
            messagebox.showerror("Error", "Could not identify model")
            self.add_log("Failed to identify model", "ERROR")
            return
        
        self.loading_model = True
        
        try:
            response = requests.post(
                f"{self.api_base}/api/v1/models/load",
                json={"model": model_key},
                timeout=30
            )
            
            if response.status_code == 200:
                self.current_model = model_key
                self.check_connection()
                messagebox.showinfo("Success", f"Model loaded: {model_key}")
                self.add_log(f"Model loaded: {model_key}", "INFO")
            else:
                messagebox.showerror("Error", f"Failed to load model.\nStatus: {response.status_code}")
                self.add_log(f"Failed to load model: {response.status_code}", "ERROR")
        except Exception as e:
            messagebox.showerror("Error", f"Error loading model:\n{str(e)}")
            self.add_log(f"Error loading model: {str(e)}", "ERROR")
        finally:
            self.loading_model = False
    
    def refresh_script(self):
        """Refresh connection and model list"""
        self.check_connection()
        self.refresh_models()
        self.add_log("Script refreshed", "INFO")
    
    def restart_script(self):
        """Restart the application"""
        if messagebox.askyesno("Restart", "Restart? Token count will reset."):
            self.root.quit()
            subprocess.Popen([sys.executable, sys.argv[0]])
            sys.exit()
    
    def check_connection(self):
        """Check LM Studio API connection"""
        try:
            response = requests.get(f"{self.api_base}/api/v1/models", timeout=2)
            self.status_label.config(text="✅ Connected", foreground="green")
            self.add_log("Connected to LM Studio", "INFO")
            
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                
                if self.current_model:
                    for model in models:
                        if model.get("key") == self.current_model:
                            display_name = model.get("display_name", "Unknown")
                            self.model_status_label.config(text="✅ Model Loaded", foreground="green")
                            self.model_name_label.config(text=display_name, foreground="blue")
                            return True
                    self.current_model = None
                
                self.model_status_label.config(text="❌ No Model Loaded", foreground="red")
                self.model_name_label.config(text="Waiting...", foreground="blue")
            return True
        except:
            self.status_label.config(text="❌ Disconnected", foreground="red")
            self.model_status_label.config(text="❌ No Model Loaded", foreground="red")
            self.model_name_label.config(text="Offline", foreground="blue")
            self.add_log("Lost connection to LM Studio", "ERROR")
            return False
    
    def send_chat_message(self):
        """Send a chat message to the currently selected chat model"""
        # Get selected chat model
        chat_model_display = self.chat_model_var.get()
        if chat_model_display == "None Selected":
            messagebox.showwarning("No Model", "Please select a model for chat.")
            return
        
        # Get the model key from display name
        chat_model_key = self.model_display_names.get(chat_model_display)
        if not chat_model_key:
            messagebox.showwarning("No Model", "Could not identify selected model.")
            return
        
        # Get user input
        user_message = self.chat_input.get("1.0", tk.END).strip()
        if not user_message:
            messagebox.showwarning("Empty Message", "Please type a message.")
            return
        
        try:
            # Display user message
            self.add_chat_message(user_message, is_user=True)
            
            # Clear input field
            self.chat_input.delete("1.0", tk.END)
            self.root.update()
            
            # Show typing indicator
            self.add_chat_message("Thinking...", is_user=False)
            self.root.update()
            
            # Build chat prompt
            chat_prompt = user_message
            
            # Send to LM Studio API
            response = requests.post(
                f"{self.api_base}/v1/chat/completions",
                json={
                    "model": chat_model_key,
                    "messages": [
                        {"role": "user", "content": chat_prompt}
                    ],
                    "temperature": self.temperature_var.get(),
                    "max_tokens": self.max_tokens_var.get()
                },
                timeout=60
            )
            
            if response.status_code == 200:
                result = response.json()
                assistant_message = result['choices'][0]['message']['content']
                
                # Extract token usage and update parent app
                if 'usage' in result:
                    tokens_used = result['usage'].get('total_tokens', 0)
                    self.reasoning_tokens = tokens_used
                    self.session_tokens += tokens_used
                    
                    # Update token display
                    self.reasoning_tokens_label.config(text=str(self.reasoning_tokens))
                    self.session_tokens_label.config(text=str(self.session_tokens))
                
                # Remove typing indicator and add real response
                if self.chat_messages_frame.winfo_children():
                    self.chat_messages_frame.winfo_children()[-1].destroy()
                self.add_chat_message(assistant_message, is_user=False)
            else:
                if self.chat_messages_frame.winfo_children():
                    self.chat_messages_frame.winfo_children()[-1].destroy()
                self.add_chat_message(f"API Error: {response.status_code}", is_user=False)
        
        except requests.exceptions.RequestException as e:
            if self.chat_messages_frame.winfo_children():
                self.chat_messages_frame.winfo_children()[-1].destroy()
            self.add_chat_message(f"Failed to connect to LM Studio:\n{str(e)}", is_user=False)
        except KeyError:
            if self.chat_messages_frame.winfo_children():
                self.chat_messages_frame.winfo_children()[-1].destroy()
            self.add_chat_message("Unexpected response format from LM Studio.", is_user=False)
    
    def add_chat_message(self, text, is_user=True):
        """Add a message bubble to the chat"""
        msg_frame = tk.Frame(self.chat_messages_frame, bg="white")
        msg_frame.pack(fill=tk.X, padx=10, pady=5, anchor=tk.E if is_user else tk.W)
        
        # Get current canvas width for wraplength
        canvas_width = self.chat_canvas.winfo_width()
        if canvas_width < 2:
            canvas_width = 300  # Default width
        max_width = max(canvas_width - 80, 150)
        
        if is_user:
            # User message (right, blue)
            bubble_frame = tk.Frame(msg_frame, bg="#0084ff", relief=tk.FLAT)
            bubble_frame.pack(side=tk.RIGHT, padx=(50, 0), fill=tk.X, expand=False)
            
            msg_label = tk.Label(bubble_frame, text=text, font=("Arial", 9), 
                                fg="white", bg="#0084ff", wraplength=max_width, justify=tk.LEFT, padx=12, pady=8)
            msg_label.pack(fill=tk.BOTH, expand=True)
        else:
            # Assistant message (left, gray)
            bubble_frame = tk.Frame(msg_frame, bg="#e4e6eb", relief=tk.FLAT)
            bubble_frame.pack(side=tk.LEFT, padx=(0, 50), fill=tk.X, expand=False)
            
            msg_label = tk.Label(bubble_frame, text=text, font=("Arial", 9), 
                                fg="#000", bg="#e4e6eb", wraplength=max_width, justify=tk.LEFT, padx=12, pady=8)
            msg_label.pack(fill=tk.BOTH, expand=True)
        
        # Store label for later updates
        self.chat_message_labels.append(msg_label)
        
        # Auto-scroll to bottom
        self.chat_messages_frame.update_idletasks()
        self.chat_canvas.yview_moveto(1)
    
    def clear_chat_messages(self):
        """Clear all chat messages"""
        for widget in self.chat_messages_frame.winfo_children():
            widget.destroy()
        self.chat_message_labels.clear()
    
    def update_cascade_models(self):
        """Update cascade pass dropdowns when models list changes"""
        try:
            response = requests.get(f"{self.api_base}/api/v1/models", timeout=5)
            if response.status_code == 200:
                data = response.json()
                models = data.get("models", [])
                llm_models = [m for m in models if m.get("type") == "llm"]
                
                model_options = ["None Selected"]  # Add None Selected as first option
                self.model_display_names = {}
                
                for model in llm_models:
                    display_name = model.get("display_name", "Unknown")
                    key = model.get("key", "unknown")
                    quantization = model.get("quantization", {}).get("name", "")
                    display_text = f"{display_name} ({quantization})" if quantization else display_name
                    
                    model_options.append(display_text)
                    self.model_display_names[display_text] = key
                
                # Update all 5 pass dropdowns with available models
                for pass_num in range(1, 6):
                    if pass_num in self.cascade_pass_dropdowns:
                        self.cascade_pass_dropdowns[pass_num]['values'] = model_options
                
                # Update chat model dropdown
                self.chat_model_dropdown['values'] = model_options
        except Exception as e:
            self.add_log(f"Error updating cascade models: {str(e)}", "ERROR")
    
    def start_cascade(self):
        """Start the 5-pass story cascade refinement"""
        if self.cascade_running:
            messagebox.showwarning("In Progress", "Cascade is already running.")
            return
        
        # Get selected models for each pass
        selected_passes = []
        for pass_num in range(1, 6):
            model_display = self.cascade_pass_vars[pass_num].get()
            if model_display != "None Selected":
                model_key = self.model_display_names.get(model_display)
                if model_key:
                    selected_passes.append((pass_num, model_display, model_key))
        
        if not selected_passes:
            messagebox.showwarning("No Models", "Select at least one model for a pass.")
            self.add_log("Cascade error: no models selected", "WARNING")
            return
        
        # Get initial prompt
        story_prompt = self.cascade_input.get("1.0", tk.END).strip()
        if not story_prompt:
            messagebox.showwarning("Empty Prompt", "Enter a story idea first.")
            self.add_log("Cascade error: empty prompt", "WARNING")
            return
        
        # Clear output
        self.cascade_output.config(state=tk.NORMAL)
        self.cascade_output.delete("1.0", tk.END)
        self.cascade_output.config(state=tk.DISABLED)
        
        self.cascade_running = True
        self.add_log(f"Starting 5-pass cascade with {len(selected_passes)} models", "INFO")
        
        # Run cascade in background thread
        def cascade_thread():
            try:
                current_story = story_prompt
                total_tokens = 0
                
                # Process through each pass
                for pass_num, model_name, model_key in selected_passes:
                    self.add_log(f"Pass {pass_num}/{len(selected_passes)}: {model_name}", "INFO")
                    
                    # Build pass-specific prompts
                    if pass_num == 1:
                        # Pass 1: Create story from prompt
                        refine_prompt = f"Write a creative and engaging short story based on this idea:\n\n{current_story}\n\nMake it around 300-400 words with vivid descriptions and interesting characters."
                    elif pass_num == 2:
                        # Pass 2: First refinement
                        refine_prompt = f"Improve this story by enhancing the narrative flow and adding more descriptive detail:\n\n{current_story}\n\nKeep it around 400-500 words."
                    elif pass_num == 3:
                        # Pass 3: Character development
                        refine_prompt = f"Deepen the story by developing character motivations, emotions, and dialogue. Make the interactions more realistic:\n\n{current_story}\n\nKeep it around 450-550 words."
                    elif pass_num == 4:
                        # Pass 4: Pacing and tension
                        refine_prompt = f"Refine the story for better pacing, build tension, and create a more compelling narrative arc:\n\n{current_story}\n\nKeep it around 450-550 words."
                    else:  # Pass 5
                        # Pass 5: Final polish
                        refine_prompt = f"Polish this story to publication quality. Enhance prose, ensure strong voice, perfect pacing, and create a satisfying conclusion:\n\n{current_story}\n\nFinal version should be 400-600 words of polished prose."
                    
                    # Call model with per-pass token limit
                    response = requests.post(
                        f"{self.api_base}/v1/chat/completions",
                        json={
                            "model": model_key,
                            "messages": [{"role": "user", "content": refine_prompt}],
                            "temperature": self.cascade_temp_var.get(),
                            "max_tokens": self.cascade_pass_max_tokens[pass_num].get()  # Use per-pass token limit
                        },
                        timeout=120
                    )
                    
                    if response.status_code == 200:
                        result = response.json()
                        current_story = result['choices'][0]['message']['content']
                        
                        # Track tokens
                        if 'usage' in result:
                            tokens_used = result['usage'].get('total_tokens', 0)
                            total_tokens += tokens_used
                            self.reasoning_tokens = tokens_used
                            self.session_tokens += tokens_used
                        
                        # ONLY update UI for the LAST pass (final output)
                        is_last_pass = (pass_num == selected_passes[-1][0])
                        if is_last_pass:
                            self.cascade_output.config(state=tk.NORMAL)
                            self.cascade_output.delete("1.0", tk.END)
                            self.cascade_output.insert(tk.END, current_story)
                            self.cascade_output.config(state=tk.DISABLED)
                            
                            # Auto-scroll to top
                            self.cascade_output.see("1.0")
                        
                        self.root.update()
                    else:
                        error_msg = f"API Error ({response.status_code}) from {model_name} (Pass {pass_num})"
                        self.cascade_output.config(state=tk.NORMAL)
                        self.cascade_output.delete("1.0", tk.END)
                        self.cascade_output.insert(tk.END, f"❌ {error_msg}\n")
                        self.cascade_output.config(state=tk.DISABLED)
                        self.add_log(error_msg, "ERROR")
                        break
                
                # Update token display
                self.reasoning_tokens_label.config(text=str(total_tokens))
                self.session_tokens_label.config(text=str(self.session_tokens))
                
                self.add_log(f"Cascade complete: {total_tokens} total tokens", "INFO")
                
            except requests.exceptions.RequestException as e:
                error_msg = f"Connection error: {str(e)}"
                self.cascade_output.config(state=tk.NORMAL)
                self.cascade_output.delete("1.0", tk.END)
                self.cascade_output.insert(tk.END, f"❌ {error_msg}\n")
                self.cascade_output.config(state=tk.DISABLED)
                self.add_log(error_msg, "ERROR")
            
            except Exception as e:
                error_msg = f"Error: {str(e)}"
                self.cascade_output.config(state=tk.NORMAL)
                self.cascade_output.delete("1.0", tk.END)
                self.cascade_output.insert(tk.END, f"❌ {error_msg}\n")
                self.cascade_output.config(state=tk.DISABLED)
                self.add_log(error_msg, "ERROR")
            
            finally:
                self.cascade_running = False
        
        thread = threading.Thread(target=cascade_thread, daemon=True)
        thread.start()
    
    def clear_cascade(self):
        """Clear cascade inputs and outputs"""
        self.cascade_input.delete("1.0", tk.END)
        self.cascade_output.config(state=tk.NORMAL)
        self.cascade_output.delete("1.0", tk.END)
        self.cascade_output.config(state=tk.DISABLED)
        # Reset all per-pass token limits to default
        for pass_num in range(1, 6):
            self.cascade_pass_max_tokens[pass_num].set(2000)
        self.add_log("Cascade cleared", "INFO")
    
    def copy_cascade_output(self):
        """Copy cascade output to clipboard"""
        text = self.cascade_output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Empty", "No story to copy")
            return
        
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        messagebox.showinfo("Copied", "Story cascade output copied to clipboard!")
        self.add_log("Copied cascade output", "INFO")
    
    def save_cascade_story(self):
        """Save the current cascade story to file"""
        text = self.cascade_output.get("1.0", tk.END).strip()
        if not text:
            messagebox.showwarning("Empty", "No story to save")
            return
        
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"story_cascade_{timestamp}.txt"
        filepath = os.path.join(os.path.expanduser("~"), "Downloads", filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(text)
            
            messagebox.showinfo("Saved", f"Story saved:\n{filename}")
            self.add_log(f"Saved cascade story: {filename}", "INFO")
        except Exception as e:
            messagebox.showerror("Error", f"Could not save:\n{str(e)}")
            self.add_log(f"Error saving story: {str(e)}", "ERROR")


if __name__ == "__main__":
    root = tk.Tk()
    root.withdraw()
    app = LMStudioOrchestrator(root)
    root.mainloop()
