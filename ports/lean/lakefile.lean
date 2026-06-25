import Lake
open Lake DSL

package «lorentzian-classification-lean» where
  leanOptions := #[
    ⟨`autoImplicit, false⟩
  ]

@[default_target]
lean_lib «LorentzianClassification» where
  srcDir := "."

@[default_target]
lean_exe «lorentzian-classification» where
  root := `Main

@[test_driver]
lean_exe «tests» where
  root := `Tests
