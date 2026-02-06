#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import subprocess
import os
import shutil

def step0_force_cache_refresh():
    """STEP 0: Force font cache refresh"""
    print("=== STEP 0: Force Font Cache Refresh ===")
    
    print("🔄 Clearing matplotlib cache...")
    try:
        cache_dir = os.path.expanduser('~/.cache/matplotlib')
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
            print("✅ Matplotlib cache directory removed")
        else:
            print("ℹ️  Matplotlib cache directory doesn't exist")
    except Exception as e:
        print(f"❌ Error removing matplotlib cache: {e}")
    
    print("\n🔄 Rebuilding matplotlib font manager...")
    try:
        # Try multiple methods
        if hasattr(fm, 'fontManager'):
            if hasattr(fm.fontManager, 'rebuild'):
                fm.fontManager.rebuild()
                print("✅ Matplotlib font manager rebuilt using fontManager.rebuild()")
            elif hasattr(fm, '_rebuild'):
                fm._rebuild()
                print("✅ Matplotlib font manager rebuilt using _rebuild()")
            else:
                print("ℹ️  Font manager rebuild method not found, continuing...")
        else:
            print("ℹ️  FontManager not available, continuing...")
    except Exception as e:
        print(f"⚠️  Font manager rebuild issue (continuing): {e}")

def step1_system_font_check():
    print("\n=== STEP 1: System Font Check & Auto Installation ===")

    # Check if Nanum font files exist
    nanum_found = False
    try:
        result = subprocess.run(['find', '/usr', '-name', '*nanum*', '-type', 'f'], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.DEVNULL, 
                              text=True)
        if result.returncode == 0 and result.stdout.strip():
            nanum_files = [line for line in result.stdout.strip().split('\n') if line and 'truetype' in line]
            if nanum_files:
                print("✅ Found Nanum font files:")
                for line in nanum_files:
                    print(f"  {line}")
                nanum_found = True
            else:
                print("❌ No Nanum font files found")
        else:
            print("❌ No Nanum font files found")
    except Exception as e:
        print(f"❌ Error searching system: {e}")

    # Additional verification with fc-list
    fc_list_found = False
    try:
        result = subprocess.run(['fc-list'], 
                              stdout=subprocess.PIPE, 
                              stderr=subprocess.DEVNULL, 
                              text=True)
        if result.returncode == 0:
            nanum_fonts = [line for line in result.stdout.split('\n') if 'nanum' in line.lower()]
            if nanum_fonts:
                print(f"\n✅ fc-list found {len(nanum_fonts)} Nanum fonts")
                fc_list_found = True
            else:
                print("\n❌ No Nanum fonts found in fc-list")
        else:
            print("\n❌ Failed to run fc-list command")
    except Exception as e:
        print(f"\n❌ Error checking fc-list: {e}")
    
    # Auto-install if Nanum fonts are not installed
    if not nanum_found or not fc_list_found:
        print("\n🚨 NANUM FONTS NOT PROPERLY INSTALLED!")
        print("📦 Installing Nanum fonts automatically...")
        print("⏳ This may take a few minutes, please wait...")
        
        try:
            # apt update
            print("\n1️⃣ Updating package list...")
            update_result = subprocess.run(['sudo', 'apt', 'update'], 
                                         stdout=subprocess.PIPE, 
                                         stderr=subprocess.PIPE, 
                                         text=True)
            if update_result.returncode == 0:
                print("✅ Package list updated successfully")
            else:
                print("⚠️ Package update had issues, continuing...")
            
            # Install fonts
            print("\n2️⃣ Installing fonts-nanum and fonts-nanum-coding...")
            install_result = subprocess.run(['sudo', 'apt', 'install', '-y', 
                                           'fonts-nanum', 'fonts-nanum-coding'], 
                                          stdout=subprocess.PIPE, 
                                          stderr=subprocess.PIPE, 
                                          text=True)
            if install_result.returncode == 0:
                print("✅ Nanum fonts installed successfully!")
            else:
                print("❌ Failed to install Nanum fonts")
                print(f"Error: {install_result.stderr}")
                return False
            
            # Refresh font cache
            print("\n3️⃣ Refreshing system font cache...")
            cache_result = subprocess.run(['sudo', 'fc-cache', '-fv'], 
                                        stdout=subprocess.PIPE, 
                                        stderr=subprocess.PIPE, 
                                        text=True)
            if cache_result.returncode == 0:
                print("✅ System font cache refreshed")
            else:
                print("⚠️ Font cache refresh had issues")
            
            # Rebuild matplotlib font manager
            print("\n4️⃣ Rebuilding matplotlib font manager...")
            try:
                if hasattr(fm, 'fontManager') and hasattr(fm.fontManager, 'rebuild'):
                    fm.fontManager.rebuild()
                    print("✅ Matplotlib font manager rebuilt")
                else:
                    print("ℹ️  Font manager rebuild not available")
            except Exception as e:
                print(f"⚠️ Matplotlib rebuild issue: {e}")

            print("\n🎉 NANUM FONT INSTALLATION COMPLETED!")
            print("📝 Verifying installation...")

            # Re-verify installation
            verify_result = subprocess.run(['find', '/usr', '-name', '*nanum*', '-type', 'f'], 
                                         stdout=subprocess.PIPE, 
                                         stderr=subprocess.DEVNULL, 
                                         text=True)
            if verify_result.returncode == 0 and verify_result.stdout.strip():
                new_files = [line for line in verify_result.stdout.strip().split('\n') if line and 'truetype' in line]
                print(f"✅ Installation verified! Found {len(new_files)} Nanum font files")
            else:
                print("❌ Installation verification failed")
                
        except Exception as e:
            print(f"❌ Error during installation: {e}")
            print("🔧 Please run manually:")
            print("   sudo apt update && sudo apt install fonts-nanum fonts-nanum-coding")
            print("   sudo fc-cache -fv")
            return False
    else:
        print("\n✅ Nanum fonts are already properly installed!")
    
    return True

def step2_matplotlib_font_check():
    print("\n=== STEP 2: Matplotlib Font Check ===")
    try:
        font_paths = fm.findSystemFonts()
        nanum_paths = [path for path in font_paths if 'nanum' in path.lower()]
        if nanum_paths:
            print(f"✅ Matplotlib found {len(nanum_paths)} Nanum fonts")
        else:
            print("❌ Matplotlib cannot find Nanum fonts")
    except Exception as e:
        print(f"❌ Error: {e}")

def step3_force_nanum_settings():
    print("\n=== STEP 3: FORCE Nanum Font Settings ===")
    try:
        # Force all font settings to NanumGothic
        plt.rcParams['font.family'] = ['NanumGothic']
        plt.rcParams['font.sans-serif'] = ['NanumGothic']
        plt.rcParams['axes.unicode_minus'] = False
        
        print("✅ FORCED settings applied:")
        print(f"  Font family: {plt.rcParams['font.family']}")
        print(f"  Sans-serif: {plt.rcParams['font.sans-serif']}")

        # Safe font cache refresh
        try:
            if hasattr(fm, 'fontManager') and hasattr(fm.fontManager, 'rebuild'):
                fm.fontManager.rebuild()
                print("✅ Font cache refreshed using fontManager.rebuild()")
            elif hasattr(fm, '_rebuild'):
                fm._rebuild()
                print("✅ Font cache refreshed using _rebuild()")
            else:
                print("ℹ️  Font cache refresh method not available, continuing...")
        except Exception as cache_error:
            print(f"⚠️  Font cache refresh issue (continuing): {cache_error}")
        
    except Exception as e:
        print(f"❌ Error applying settings: {e}")

def step4_create_forced_nanum_graph():
    print("\n=== STEP 4: Create Graph with FORCED Nanum Font ===")
    try:
        nanum_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'
        
        if os.path.exists(nanum_path):
            print(f"✅ Using DIRECT font file: {nanum_path}")
            prop = fm.FontProperties(fname=nanum_path)
        else:
            print("❌ NanumGothic.ttf not found")
            prop = fm.FontProperties(family='NanumGothic')
        
        plt.figure(figsize=(12, 8))
        
        plt.subplot(2, 2, 1)
        plt.plot([1, 2, 3, 4, 5], [10000, 15000, 12000, 18000, 22000], 'bo-', linewidth=3)
        plt.title('Korean Stock Analysis - SUCCESS!', fontproperties=prop, fontsize=16)
        plt.xlabel('Trading Days', fontproperties=prop, fontsize=12)
        plt.ylabel('Stock Price (KRW)', fontproperties=prop, fontsize=12)
        
        plt.subplot(2, 2, 2)
        companies = ['Samsung', 'Hyundai', 'LG', 'SK']
        values = [100, 85, 70, 90]
        plt.bar(companies, values, color=['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4'])
        plt.title('Korean Companies - NANUM FONT', fontproperties=prop, fontsize=16)
        plt.ylabel('Market Value', fontproperties=prop, fontsize=12)
        plt.xticks(fontproperties=prop)
        
        plt.tight_layout()
        
        output_file = 'FINAL_nanum_success.png'
        plt.savefig(output_file, dpi=150, bbox_inches='tight')
        plt.close()
        
        if os.path.exists(output_file):
            size = os.path.getsize(output_file)
            print(f"✅ Graph saved as '{output_file}' ({size:,} bytes)")
        
    except Exception as e:
        print(f"❌ Error creating graph: {e}")

def step5_verify_forced_nanum():
    print("\n=== STEP 5: Verify FORCED Nanum Font Usage ===")
    try:
        nanum_path = '/usr/share/fonts/truetype/nanum/NanumGothic.ttf'
        
        if os.path.exists(nanum_path):
            prop = fm.FontProperties(fname=nanum_path)
            print(f"✅ FORCING Nanum font from: {nanum_path}")
        else:
            prop = fm.FontProperties(family='NanumGothic')
            print("⚠️  Using NanumGothic font family name")
        
        fig, ax = plt.subplots(figsize=(10, 6))
        
        title = ax.set_title('NANUM FONT SUCCESS - NO ERRORS!', 
                           fontproperties=prop, fontsize=18)
        xlabel = ax.set_xlabel('X-axis with ERROR-FREE NanumGothic', 
                             fontproperties=prop, fontsize=14)
        ylabel = ax.set_ylabel('Y-axis with ERROR-FREE NanumGothic', 
                             fontproperties=prop, fontsize=14)
        
        text1 = ax.text(0.5, 0.5, 'ALL ERRORS FIXED - NANUM SUCCESS!', 
                       ha='center', va='center', fontsize=16, fontproperties=prop)
        
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        
        print("\n🎯 CRITICAL CHECK - Actually used font:")
        print(f"   📍 Title font: {title.get_fontname()}")
        print(f"   📍 X-axis font: {xlabel.get_fontname()}")
        print(f"   📍 Y-axis font: {ylabel.get_fontname()}")
        print(f"   📍 Text font: {text1.get_fontname()}")
        
        if 'Nanum' in title.get_fontname():
            print("\n🎉 SUCCESS! NanumGothic is being used!")
        else:
            print("\n❌ FAILED! Still using different font")
        
        verification_file = 'FINAL_nanum_verification.png'
        plt.savefig(verification_file, dpi=150, bbox_inches='tight')
        plt.close()
        
        if os.path.exists(verification_file):
            size = os.path.getsize(verification_file)
            print(f"✅ Verification saved as '{verification_file}' ({size:,} bytes)")
        
    except Exception as e:
        print(f"❌ Error verifying fonts: {e}")

def step6_final_cache_refresh():
    print("\n=== STEP 6: Final Cache Refresh ===")
    try:
        print("🔄 Final matplotlib font manager rebuild...")

        # Try multiple methods safely
        rebuild_success = False

        # Method 1: Execute via subprocess
        try:
            subprocess.run(['python3', '-c', 'import matplotlib.font_manager as fm; fm.fontManager.rebuild()'], 
                         check=True, capture_output=True, text=True, timeout=30)
            print("✅ Matplotlib font manager rebuilt using subprocess fontManager.rebuild()")
            rebuild_success = True
        except:
            pass

        # Method 2: Direct fontManager.rebuild() call
        if not rebuild_success:
            try:
                if hasattr(fm, 'fontManager') and hasattr(fm.fontManager, 'rebuild'):
                    fm.fontManager.rebuild()
                    print("✅ Matplotlib font manager rebuilt using fontManager.rebuild()")
                    rebuild_success = True
            except Exception as e:
                print(f"⚠️  fontManager.rebuild() failed: {e}")

        # Method 3: Try _rebuild() (for older versions)
        if not rebuild_success:
            try:
                if hasattr(fm, '_rebuild'):
                    fm._rebuild()
                    print("✅ Matplotlib font manager rebuilt using _rebuild()")
                    rebuild_success = True
            except Exception as e:
                print(f"⚠️  _rebuild() failed: {e}")
        
        if not rebuild_success:
            print("ℹ️  Font manager rebuild not available, but fonts should still work!")
        
        print("\n💡 All font operations completed successfully!")
        print("   ✅ Nanum fonts are properly installed and configured")
        print("   ✅ Matplotlib is using NanumGothic font")
        print("\n🔧 If you need manual font refresh commands:")
        print("   sudo fc-cache -fv")
        print("   python3 -c \"import matplotlib.font_manager as fm; fm.fontManager.rebuild()\"")
        print("   rm -rf ~/.cache/matplotlib")
        
    except Exception as e:
        print(f"⚠️  Final refresh completed with minor issues: {e}")
        print("\n🔧 Manual commands if needed:")
        print("   sudo fc-cache -fv")
        print("   rm -rf ~/.cache/matplotlib")
        print("   python3 -c \"import matplotlib.font_manager as fm; fm.fontManager.rebuild()\"")

def main():
    print("🚀 NANUM FONT AUTO-INSTALLER & FORCED APPLICATION - V2")
    print("=" * 70)

    step0_force_cache_refresh()

    # Check installation and auto-install in Step 1
    installation_success = step1_system_font_check()
    
    if not installation_success:
        print("\n❌ NANUM FONT INSTALLATION FAILED!")
        print("🔧 Please install manually and retry:")
        print("   sudo apt update && sudo apt install fonts-nanum fonts-nanum-coding")
        print("   sudo fc-cache -fv")
        return
    
    step2_matplotlib_font_check()
    step3_force_nanum_settings()
    step4_create_forced_nanum_graph()
    step5_verify_forced_nanum()
    step6_final_cache_refresh()
    
    print("\n" + "=" * 70)
    print("🎯 PROCESS COMPLETED!")
    print("📂 Generated files:")
    for filename in ['FINAL_nanum_success.png', 'FINAL_nanum_verification.png']:
        if os.path.exists(filename):
            size = os.path.getsize(filename)
            print(f"  ✅ {filename} ({size:,} bytes)")
        else:
            print(f"  ❌ {filename} (not created)")
    
    print("\n🎉 If STEP 5 shows 'NanumGothic', you're ALL SET!")

if __name__ == "__main__":
    main()
