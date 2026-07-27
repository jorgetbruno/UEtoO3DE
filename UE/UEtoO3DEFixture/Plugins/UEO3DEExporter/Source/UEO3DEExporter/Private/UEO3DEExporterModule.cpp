// UEO3DEExporter — M0 skeleton: one toolbar button that logs "hello".
// Real export functionality arrives from M1 onward (see ue-to-o3de-milestone-plan-v2.md).

#include "Modules/ModuleManager.h"
#include "ToolMenus.h"
#include "Framework/Commands/UIAction.h"
#include "Styling/AppStyle.h"

DEFINE_LOG_CATEGORY_STATIC(LogUEO3DEExporter, Log, All);

class FUEO3DEExporterModule : public IModuleInterface
{
public:
	virtual void StartupModule() override
	{
		UToolMenus::RegisterStartupCallback(
			FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FUEO3DEExporterModule::RegisterMenus));
	}

	virtual void ShutdownModule() override
	{
		UToolMenus::UnRegisterStartupCallback(this);
		UToolMenus::UnregisterOwner(this);
	}

private:
	static void LogHello()
	{
		UE_LOG(LogUEO3DEExporter, Log, TEXT("UEO3DEExporter: hello"));
	}

	void RegisterMenus()
	{
		FToolMenuOwnerScoped OwnerScoped(this);

		UToolMenu* ToolbarMenu = UToolMenus::Get()->ExtendMenu("LevelEditor.LevelEditorToolBar.PlayToolBar");
		FToolMenuSection& Section = ToolbarMenu->FindOrAddSection("UEtoO3DE");

		FToolMenuEntry Entry = FToolMenuEntry::InitToolBarButton(
			"UEO3DEExporter_Hello",
			FUIAction(FExecuteAction::CreateStatic(&FUEO3DEExporterModule::LogHello)),
			NSLOCTEXT("UEO3DEExporter", "HelloLabel", "Export to O3DE"),
			NSLOCTEXT("UEO3DEExporter", "HelloTooltip", "UEO3DEExporter placeholder (M0) — logs hello."),
			FSlateIcon(FAppStyle::GetAppStyleSetName(), "Icons.Export"));
		Section.AddEntry(Entry);
	}
};

IMPLEMENT_MODULE(FUEO3DEExporterModule, UEO3DEExporter)
